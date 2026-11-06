"""SQLite storage: raw captures, one row per ad, a price/status observation per sighting, ad details."""

from __future__ import annotations

import itertools
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from carwatch.models import AdDetail, AdSummary, Capture, IngestResult, PriceChange, SearchItem

SCHEMA_VERSION = 3

# A reload of the same page within this window does not count as a new sighting.
DEDUPE_WINDOW = timedelta(minutes=10)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY,
    captured_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    page_url TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    brand TEXT, model TEXT, year INTEGER, mileage_km INTEGER, fuel TEXT,
    engine_cc INTEGER, power_kw INTEGER, horse_power INTEGER, gearbox TEXT, chassis TEXT,
    doors TEXT, seats TEXT, city TEXT, is_dealer INTEGER, condition_new INTEGER,
    registration_in_price INTEGER, image_url TEXT, images_count INTEGER, currency TEXT,
    status TEXT, price INTEGER, base_price INTEGER, discount_pct REAL,
    first_price INTEGER, min_price INTEGER, max_price INTEGER,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    published_at TEXT, lifted_at TEXT, site_updated_at TEXT,
    has_details INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    ad_id INTEGER NOT NULL REFERENCES ads(id),
    capture_id INTEGER NOT NULL REFERENCES captures(id),
    seen_at TEXT NOT NULL,
    source TEXT NOT NULL,
    search_key TEXT,
    price INTEGER, base_price INTEGER, discount_pct REAL, status TEXT
);
CREATE INDEX IF NOT EXISTS ix_observations_ad ON observations(ad_id, seen_at);
CREATE INDEX IF NOT EXISTS ix_observations_search ON observations(search_key);
CREATE TABLE IF NOT EXISTS ad_details (
    ad_id INTEGER PRIMARY KEY REFERENCES ads(id),
    captured_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    key TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    page_url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    total_items INTEGER,
    times_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    root_query TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    note TEXT,
    auto INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    kind TEXT NOT NULL,
    query TEXT,
    page INTEGER,
    ad_id INTEGER,
    day TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    leased_at TEXT,
    created_at TEXT NOT NULL,
    done_at TEXT,
    total_items INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_pending ON tasks(status, kind, id);
CREATE INDEX IF NOT EXISTS ix_tasks_run ON tasks(run_id, kind, status);
CREATE INDEX IF NOT EXISTS ix_tasks_detail ON tasks(kind, ad_id);
CREATE TABLE IF NOT EXISTS crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS valuations (
    ad_id INTEGER PRIMARY KEY REFERENCES ads(id),
    expected_price INTEGER NOT NULL,
    deal_pct REAL NOT NULL,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ads_last_seen ON ads(last_seen);
"""

# Ad columns that are plain copies of a SearchItem/AdDetail field (column name -> model field name).
_AD_COLUMNS: dict[str, str] = {
    "title": "title",
    "url": "url",
    "brand": "brand",
    "model": "model",
    "year": "year",
    "mileage_km": "mileage_km",
    "fuel": "fuel",
    "engine_cc": "engine_cc",
    "power_kw": "power_kw",
    "horse_power": "horse_power",
    "gearbox": "gearbox",
    "chassis": "chassis",
    "doors": "doors",
    "seats": "seats",
    "city": "city",
    "is_dealer": "is_dealer",
    "condition_new": "condition_new",
    "registration_in_price": "registration_in_price",
    "image_url": "image_url",
    "images_count": "images_count",
    "currency": "currency",
    "lifted_at": "lifted_at",
    "site_updated_at": "updated_at",
}

SORTS: dict[str, str] = {
    "last_seen": "a.last_seen DESC",
    "first_seen": "a.first_seen DESC",
    "price": "a.price IS NULL, a.price ASC",
    "price_desc": "a.price IS NULL, a.price DESC",
    "drop": "(a.price - a.max_price) ASC, a.last_seen DESC",
    "mileage": "a.mileage_km IS NULL, a.mileage_km ASC",
    "year": "a.year IS NULL, a.year DESC",
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def search_key(query: dict[str, object]) -> str:
    """Stable key for a search: its filters without the page number."""
    filters = {k: v for k, v in query.items() if k != "page"}
    return json.dumps(filters, sort_keys=True, ensure_ascii=False)


@dataclass
class AdFilters:
    q: str | None = None
    brand: str | None = None
    model: str | None = None
    search: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    year_min: int | None = None
    km_max: int | None = None
    dropped: bool = False
    active_only: bool = False
    sort: str = "last_seen"
    limit: int = 500


def _upgrade(conn: sqlite3.Connection) -> None:
    """Add what a database made by an older version is missing (CREATE TABLE IF NOT EXISTS skips existing tables)."""
    if "auto" not in {row[1] for row in conn.execute("PRAGMA table_info(runs)")}:
        conn.execute("ALTER TABLE runs ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
        # Older databases do not say who started a run. A running full pass is treated as automatic: with the switch
        # off it waits, and "Pokreni ceo sajt" claims it as the user's.
        conn.execute("UPDATE runs SET auto = 1 WHERE kind = 'full' AND status = 'running'")


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            _upgrade(conn)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- writes -------------------------------------------------------------------------------

    def ingest(self, capture: Capture, received_at: datetime | None = None) -> IngestResult:
        seen_at = _iso(capture.captured_at)
        received = _iso(received_at or datetime.now(UTC))
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO captures (captured_at, received_at, kind, page_url, payload) VALUES (?, ?, ?, ?, ?)",
                (seen_at, received, capture.kind, capture.page_url, capture.model_dump_json()),
            )
            capture_id = int(cur.lastrowid or 0)

            key: str | None = None
            # Searches you open yourself are listed under "Pretrage"; the worker's price bands are not.
            if capture.kind == "search" and capture.search is not None and capture.task_id is None:
                key = search_key(capture.search.query)
                self._upsert_search(conn, key, capture, seen_at)

            items: list[SearchItem] = list(capture.items)
            if capture.kind == "ad" and capture.ad is not None:
                items = [capture.ad]

            new_ads = 0
            changes: list[PriceChange] = []
            for item in items:
                is_new, change = self._upsert_ad(conn, item, seen_at)
                new_ads += int(is_new)
                if change is not None:
                    changes.append(change)
                self._observe(conn, item, capture_id, seen_at, capture.kind, key)
                if isinstance(item, AdDetail):
                    self._upsert_details(conn, item, seen_at)

        return IngestResult(capture_id=capture_id, ads_seen=len(items), new_ads=new_ads, price_changes=changes)

    def _upsert_search(self, conn: sqlite3.Connection, key: str, capture: Capture, seen_at: str) -> None:
        assert capture.search is not None
        conn.execute(
            """
            INSERT INTO searches (key, query, page_url, first_seen, last_seen, total_items, times_seen)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(key) DO UPDATE SET
                page_url = CASE WHEN excluded.last_seen >= searches.last_seen THEN excluded.page_url
                                ELSE searches.page_url END,
                total_items = CASE WHEN excluded.last_seen >= searches.last_seen THEN excluded.total_items
                                   ELSE searches.total_items END,
                last_seen = MAX(searches.last_seen, excluded.last_seen),
                first_seen = MIN(searches.first_seen, excluded.first_seen),
                times_seen = searches.times_seen + 1
            """,
            (key, key, capture.page_url, seen_at, seen_at, capture.search.total_items),
        )

    def _upsert_ad(self, conn: sqlite3.Connection, item: SearchItem, seen_at: str) -> tuple[bool, PriceChange | None]:
        row = conn.execute("SELECT price, last_seen FROM ads WHERE id = ?", (item.id,)).fetchone()
        values = {col: getattr(item, attr) for col, attr in _AD_COLUMNS.items()}
        if isinstance(item, AdDetail):
            values["published_at"] = item.published_at

        if row is None:
            columns = [
                *values,
                "id",
                "status",
                "price",
                "base_price",
                "discount_pct",
                "first_price",
                "min_price",
                "max_price",
                "first_seen",
                "last_seen",
            ]
            insert_params = [
                *values.values(),
                item.id,
                item.status,
                item.price,
                item.base_price,
                item.discount_pct,
                item.price,
                item.price,
                item.price,
                seen_at,
                seen_at,
            ]
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(f"INSERT INTO ads ({', '.join(columns)}) VALUES ({placeholders})", insert_params)
            return True, None

        is_latest = seen_at >= row["last_seen"]
        change: PriceChange | None = None
        if is_latest and row["price"] is not None and item.price is not None and item.price != row["price"]:
            change = PriceChange(ad_id=item.id, title=item.title, old_price=row["price"], new_price=item.price)

        # Descriptive fields: keep the newest non-null value. Current price/status: only from the
        # newest sighting, so a delayed capture (sent later from the extension's outbox) cannot
        # overwrite a fresher one.
        sets = [f"{col} = COALESCE(?, {col})" for col in values]
        params: list[Any] = list(values.values())
        if is_latest:
            sets += [
                "status = COALESCE(?, status)",
                "price = COALESCE(?, price)",
                "base_price = COALESCE(?, base_price)",
                "discount_pct = ?",
                "last_seen = ?",
            ]
            params += [item.status, item.price, item.base_price, item.discount_pct, seen_at]
        if item.price is not None:
            sets += [
                "min_price = MIN(COALESCE(min_price, ?), ?)",
                "max_price = MAX(COALESCE(max_price, ?), ?)",
                "first_price = CASE WHEN ? < first_seen THEN ? ELSE COALESCE(first_price, ?) END",
            ]
            params += [item.price, item.price, item.price, item.price, seen_at, item.price, item.price]
        sets.append("first_seen = MIN(first_seen, ?)")
        params.append(seen_at)
        conn.execute(f"UPDATE ads SET {', '.join(sets)} WHERE id = ?", [*params, item.id])
        return False, change

    def _observe(
        self,
        conn: sqlite3.Connection,
        item: SearchItem,
        capture_id: int,
        seen_at: str,
        source: str,
        key: str | None,
    ) -> None:
        last = conn.execute(
            "SELECT seen_at, price, status FROM observations WHERE ad_id = ? ORDER BY seen_at DESC LIMIT 1",
            (item.id,),
        ).fetchone()
        if last is not None and last["price"] == item.price and last["status"] == item.status:
            gap = abs(datetime.fromisoformat(seen_at) - datetime.fromisoformat(last["seen_at"]))
            if gap < DEDUPE_WINDOW:
                return
        conn.execute(
            """INSERT INTO observations (ad_id, capture_id, seen_at, source, search_key, price, base_price,
               discount_pct, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id,
                capture_id,
                seen_at,
                source,
                key,
                item.price,
                item.base_price,
                item.discount_pct,
                item.status,
            ),
        )

    def _upsert_details(self, conn: sqlite3.Connection, ad: AdDetail, seen_at: str) -> None:
        conn.execute(
            """INSERT INTO ad_details (ad_id, captured_at, data) VALUES (?, ?, ?)
               ON CONFLICT(ad_id) DO UPDATE SET captured_at = excluded.captured_at, data = excluded.data
               WHERE excluded.captured_at >= ad_details.captured_at""",
            (ad.id, seen_at, ad.model_dump_json()),
        )
        conn.execute("UPDATE ads SET has_details = 1 WHERE id = ?", (ad.id,))

    # ---- reads --------------------------------------------------------------------------------

    def summary(self, ad_id: int) -> AdSummary:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT a.*, (SELECT COUNT(DISTINCT substr(seen_at, 1, 10)) FROM observations o
                                WHERE o.ad_id = a.id) AS days_seen,
                          (SELECT COUNT(*) FROM observations o WHERE o.ad_id = a.id) AS obs_count
                   FROM ads a WHERE a.id = ?""",
                (ad_id,),
            ).fetchone()
            if row is None:
                return AdSummary(known=False, id=ad_id)
            prices = [
                r["price"]
                for r in conn.execute(
                    "SELECT price FROM observations WHERE ad_id = ? AND price IS NOT NULL ORDER BY seen_at",
                    (ad_id,),
                )
            ]
        changes = sum(1 for prev, cur in itertools.pairwise(prices) if prev != cur)
        return AdSummary(
            known=True,
            id=ad_id,
            first_seen=_parse(row["first_seen"]),
            last_seen=_parse(row["last_seen"]),
            days_seen=row["days_seen"],
            first_price=row["first_price"],
            price=row["price"],
            min_price=row["min_price"],
            max_price=row["max_price"],
            price_changes=changes,
            published_at=row["published_at"],
        )

    def list_ads(self, f: AdFilters) -> list[sqlite3.Row]:
        where: list[str] = []
        params: list[Any] = []
        if f.q:
            where.append("(a.title LIKE ? OR a.brand LIKE ? OR a.model LIKE ? OR a.city LIKE ?)")
            params += [f"%{f.q}%"] * 4
        if f.brand:
            where.append("a.brand = ?")
            params.append(f.brand)
        if f.model:
            where.append("a.model = ?")
            params.append(f.model)
        if f.search:
            where.append("a.id IN (SELECT ad_id FROM observations WHERE search_key = ?)")
            params.append(f.search)
        if f.price_min is not None:
            where.append("a.price >= ?")
            params.append(f.price_min)
        if f.price_max is not None:
            where.append("a.price <= ?")
            params.append(f.price_max)
        if f.year_min is not None:
            where.append("a.year >= ?")
            params.append(f.year_min)
        if f.km_max is not None:
            where.append("a.mileage_km <= ?")
            params.append(f.km_max)
        if f.dropped:
            where.append("a.price < a.max_price")
        if f.active_only:
            where.append("COALESCE(a.status, 'active') = 'active'")
        order = SORTS.get(f.sort, SORTS["last_seen"])
        sql = f"""
            SELECT a.*,
                   (SELECT COUNT(DISTINCT substr(seen_at, 1, 10)) FROM observations o WHERE o.ad_id = a.id)
                       AS days_seen
            FROM ads a
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY {order}
            LIMIT ?
        """
        with self.connect() as conn:
            return conn.execute(sql, [*params, f.limit]).fetchall()

    def get_ad(self, ad_id: int) -> tuple[sqlite3.Row | None, dict[str, Any] | None, list[sqlite3.Row]]:
        with self.connect() as conn:
            ad = conn.execute("SELECT * FROM ads WHERE id = ?", (ad_id,)).fetchone()
            details_row = conn.execute("SELECT data FROM ad_details WHERE ad_id = ?", (ad_id,)).fetchone()
            observations = conn.execute(
                "SELECT * FROM observations WHERE ad_id = ? ORDER BY seen_at", (ad_id,)
            ).fetchall()
        details = json.loads(details_row["data"]) if details_row else None
        return ad, details, observations

    def list_searches(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT s.*, (SELECT COUNT(DISTINCT ad_id) FROM observations o WHERE o.search_key = s.key)
                          AS ads_seen
                   FROM searches s ORDER BY s.last_seen DESC"""
            ).fetchall()

    def facets(self) -> dict[str, list[str]]:
        with self.connect() as conn:
            brands = [
                r[0] for r in conn.execute("SELECT DISTINCT brand FROM ads WHERE brand IS NOT NULL ORDER BY brand")
            ]
            models = [
                r[0] for r in conn.execute("SELECT DISTINCT model FROM ads WHERE model IS NOT NULL ORDER BY model")
            ]
        return {"brands": brands, "models": models}

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            ads = conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0]
            captures = conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
            last = conn.execute("SELECT MAX(received_at) FROM captures").fetchone()[0]
            dropped = conn.execute("SELECT COUNT(*) FROM ads WHERE price < max_price").fetchone()[0]
        return {"ads": ads, "captures": captures, "last_capture": last, "dropped": dropped}
