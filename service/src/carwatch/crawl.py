"""Collection planning for the extension's worker tab.

The service decides what to fetch; the extension only executes requests in the user's own browser and
reports back. Two kinds of work exist:

* search pages: a full pass over every passenger-car ad on the site, split into price bands because the
  site stops paging deep result lists. A band with more ads than ``partition_cap`` is halved.
* ad pages: details (damage, registration, origin, equipment) for ads that have none yet, up to a daily limit,
  best deals first.

When the site refuses requests, everything pauses for ``blocked_pause_hours``; nothing tries to get around it.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from carwatch.config import Settings
from carwatch.models import Capture, RunProgress, TaskFailure, WorkBatch, WorkStatus, WorkTask
from carwatch.store import Store, _iso

PAGE_SIZE = 25
SEARCH_PATH = "/auto-oglasi/pretraga"
FULL_ROOT: dict[str, str] = {"showOldNew": "all"}

# Starting price bands (EUR) for a full pass; dense bands get halved further when they exceed the cap.
FULL_BANDS: list[tuple[int, int | None]] = [
    (0, 999), (1000, 1999), (2000, 2999), (3000, 3999), (4000, 4999), (5000, 5999), (6000, 7499),
    (7500, 8999), (9000, 10999), (11000, 13499), (13500, 16499), (16500, 19999), (20000, 24999),
    (25000, 31999), (32000, 44999), (45000, 69999), (70000, None),
]  # fmt: skip

# Ads not seen for this long are not worth fetching details for.
DETAIL_FRESHNESS = timedelta(days=10)
# How long details wait for the fair-price model after a full pass.
VALUATION_WAIT = timedelta(minutes=15)


def band_query(base: dict[str, str], low: int, high: int | None) -> dict[str, str]:
    query = {k: v for k, v in base.items() if k not in {"priceFrom", "priceTo", "page"}}
    query["priceFrom"] = str(low)
    if high is not None:
        query["priceTo"] = str(high)
    return query


def split_band(query: dict[str, str]) -> list[dict[str, str]] | None:
    """Halve a price band; None when it cannot be split any further."""
    low = int(query.get("priceFrom", "0") or 0)
    high_raw = query.get("priceTo")
    if high_raw is None or high_raw == "":
        edge = max(low * 2, low + 1000)
        return [band_query(query, low, edge - 1), band_query(query, edge, None)]
    high = int(high_raw)
    if high <= low:
        return None
    mid = (low + high) // 2
    return [band_query(query, low, mid), band_query(query, mid + 1, high)]


class Crawler:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)

    # ---- runs -------------------------------------------------------------------------------------

    def start_full_run(self, now: datetime) -> int:
        """Start a pass over the whole site, or return the one already running."""
        with self.store.connect() as conn:
            running = self._running_run(conn, "full")
            if running is not None:
                return running
            return self._create_run(
                conn, "full", FULL_ROOT, now, [band_query(FULL_ROOT, lo, hi) for lo, hi in FULL_BANDS]
            )

    def start_search_run(self, query: dict[str, str], now: datetime) -> int:
        """Collect every page of one search (e.g. the one open in the browser)."""
        root = {k: v for k, v in query.items() if k != "page" and v not in ("", None)}
        with self.store.connect() as conn:
            return self._create_run(conn, "search", root, now, [root])

    def _create_run(
        self, conn: sqlite3.Connection, kind: str, root: dict[str, str], now: datetime,
        partitions: list[dict[str, str]],
    ) -> int:  # fmt: skip
        cur = conn.execute(
            "INSERT INTO runs (kind, root_query, started_at, status) VALUES (?, ?, ?, 'running')",
            (kind, json.dumps(root, ensure_ascii=False), _iso(now)),
        )
        run_id = int(cur.lastrowid or 0)
        for query in partitions:
            self._add_search_task(conn, run_id, query, 1, now)
        return run_id

    def _add_search_task(
        self, conn: sqlite3.Connection, run_id: int, query: dict[str, str], page: int, now: datetime
    ) -> None:
        conn.execute(
            "INSERT INTO tasks (run_id, kind, query, page, created_at) VALUES (?, 'search', ?, ?, ?)",
            (run_id, json.dumps(query, ensure_ascii=False, sort_keys=True), page, _iso(now)),
        )

    def _running_run(self, conn: sqlite3.Connection, kind: str) -> int | None:
        row = conn.execute(
            "SELECT id FROM runs WHERE kind = ? AND status = 'running' ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
        return int(row["id"]) if row else None

    def _last_full_start(self, conn: sqlite3.Connection) -> datetime | None:
        row = conn.execute("SELECT MAX(started_at) AS s FROM runs WHERE kind = 'full'").fetchone()
        return datetime.fromisoformat(row["s"]) if row and row["s"] else None

    def _maybe_start_full_run(self, conn: sqlite3.Connection, now: datetime) -> None:
        if not self.settings.auto_full_runs or self._running_run(conn, "full") is not None:
            return
        last = self._last_full_start(conn)
        if last is None or now - last >= timedelta(days=self.settings.crawl_interval_days):
            self._create_run(conn, "full", FULL_ROOT, now, [band_query(FULL_ROOT, lo, hi) for lo, hi in FULL_BANDS])

    # ---- details ----------------------------------------------------------------------------------

    def _today(self, now: datetime) -> str:
        return now.astimezone(self.tz).date().isoformat()

    def _details_planned_today(self, conn: sqlite3.Connection, now: datetime) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE kind = 'detail' AND day = ?", (self._today(now),)
        ).fetchone()
        return int(row[0])

    def _plan_details(self, conn: sqlite3.Connection, now: datetime) -> None:
        # Search pages first: details are chosen by deal score, which needs the finished pass.
        searching = conn.execute(
            "SELECT 1 FROM tasks WHERE kind = 'search' AND status IN ('pending', 'leased') LIMIT 1"
        ).fetchone()
        if searching or self._waiting_for_valuation(conn, now):
            return
        quota = self.settings.details_daily_limit - self._details_planned_today(conn, now)
        if quota <= 0:
            return
        rows = conn.execute(
            """
            SELECT a.id FROM ads a
            LEFT JOIN valuations v ON v.ad_id = a.id
            WHERE a.has_details = 0
              AND a.price IS NOT NULL
              AND a.last_seen >= ?
              AND COALESCE(a.status, 'active') = 'active'
              AND (? IS NULL OR a.price <= ?)
              AND (? IS NULL OR a.year >= ?)
              AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.kind = 'detail' AND t.ad_id = a.id
                              AND t.status IN ('pending', 'leased', 'failed'))
            ORDER BY v.deal_pct IS NULL, v.deal_pct DESC, a.first_seen DESC
            LIMIT ?
            """,
            (
                _iso(now - DETAIL_FRESHNESS),
                self.settings.details_price_max, self.settings.details_price_max,
                self.settings.details_year_min, self.settings.details_year_min,
                quota,
            ),
        ).fetchall()  # fmt: skip
        day, created = self._today(now), _iso(now)
        conn.executemany(
            "INSERT INTO tasks (kind, ad_id, day, created_at) VALUES ('detail', ?, ?, ?)",
            [(r["id"], day, created) for r in rows],
        )

    def _waiting_for_valuation(self, conn: sqlite3.Connection, now: datetime) -> bool:
        """Right after a full pass the fair prices are recomputed; wait a little so details go to the best deals."""
        row = conn.execute("SELECT MAX(finished_at) FROM runs WHERE kind = 'full' AND status = 'done'").fetchone()
        if not row or not row[0]:
            return False
        finished = datetime.fromisoformat(row[0])
        meta = conn.execute("SELECT value FROM crawl_state WHERE key = 'valuation'").fetchone()
        computed = json.loads(meta["value"]).get("computed_at") if meta and meta["value"] else None
        fresh = computed is not None and datetime.fromisoformat(computed) >= finished
        return not fresh and now - finished < VALUATION_WAIT

    # ---- pause ------------------------------------------------------------------------------------

    def paused_until(self, conn: sqlite3.Connection) -> datetime | None:
        row = conn.execute("SELECT value FROM crawl_state WHERE key = 'paused_until'").fetchone()
        return datetime.fromisoformat(row["value"]) if row and row["value"] else None

    def pause(self, until: datetime | None) -> None:
        with self.store.connect() as conn:
            self._set_pause(conn, until)

    def _set_pause(self, conn: sqlite3.Connection, until: datetime | None) -> None:
        conn.execute(
            "INSERT INTO crawl_state (key, value) VALUES ('paused_until', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_iso(until) if until else None,),
        )

    # ---- leasing ----------------------------------------------------------------------------------

    def _prepare(self, conn: sqlite3.Connection, now: datetime) -> datetime | None:
        """Reclaim expired leases and plan new work; returns the pause end if paused."""
        paused = self.paused_until(conn)
        if paused is not None and paused > now:
            return paused
        conn.execute(
            "UPDATE tasks SET status = 'pending', leased_at = NULL WHERE status = 'leased' AND leased_at < ?",
            (_iso(now - timedelta(seconds=self.settings.lease_seconds)),),
        )
        self._maybe_start_full_run(conn, now)
        self._plan_details(conn, now)
        return None

    def lease(self, now: datetime, limit: int = 10) -> WorkBatch:
        gap = self.settings.request_gap_seconds
        with self.store.connect() as conn:
            paused = self._prepare(conn, now)
            if paused is not None:
                return WorkBatch(gap_seconds=gap, paused_until=paused)
            rows = conn.execute(
                """SELECT t.*, a.url AS ad_url FROM tasks t LEFT JOIN ads a ON a.id = t.ad_id
                   WHERE t.status = 'pending'
                   ORDER BY CASE t.kind WHEN 'search' THEN 0 ELSE 1 END, t.id LIMIT ?""",
                (limit,),
            ).fetchall()
            conn.executemany(
                "UPDATE tasks SET status = 'leased', leased_at = ? WHERE id = ?",
                [(_iso(now), r["id"]) for r in rows],
            )
        return WorkBatch(tasks=[self._to_work(r) for r in rows], gap_seconds=gap)

    @staticmethod
    def _to_work(row: sqlite3.Row) -> WorkTask:
        if row["kind"] == "search":
            params = dict(json.loads(row["query"]))
            params["page"] = str(row["page"])
            return WorkTask(id=row["id"], kind="search", path=SEARCH_PATH, params=params)
        path = urlparse(row["ad_url"]).path if row["ad_url"] else f"/auto-oglasi/{row['ad_id']}/oglas"
        return WorkTask(id=row["id"], kind="detail", path=path)

    # ---- results ----------------------------------------------------------------------------------

    def complete(self, task_id: int, capture: Capture, now: datetime) -> int | None:
        """Mark a task done and plan follow-up pages. Returns the run id if this finished a run."""
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None or task["status"] == "done":
                return None
            total = capture.search.total_items if capture.search else None
            note = None
            if (
                task["kind"] == "search"
                and capture.search is not None
                and capture.search.page not in (None, task["page"])
            ):
                note = f"sajt je vratio stranu {capture.search.page}"
            conn.execute(
                "UPDATE tasks SET status = 'done', done_at = ?, total_items = ?, note = ? WHERE id = ?",
                (_iso(now), total, note, task_id),
            )
            if task["kind"] == "search" and task["page"] == 1:
                self._expand(conn, task, total or 0, now)
            return self._finish_if_done(conn, task["run_id"], now)

    def _expand(self, conn: sqlite3.Connection, task: sqlite3.Row, total: int, now: datetime) -> None:
        query = dict(json.loads(task["query"]))
        if total > self.settings.partition_cap:
            halves = split_band(query)
            if halves is not None:
                for half in halves:
                    self._add_search_task(conn, task["run_id"], half, 1, now)
                conn.execute("UPDATE tasks SET note = 'podeljeno' WHERE id = ?", (task["id"],))
                return
        pages = min(math.ceil(total / PAGE_SIZE), math.ceil(self.settings.partition_cap / PAGE_SIZE))
        for page in range(2, pages + 1):
            self._add_search_task(conn, task["run_id"], query, page, now)

    def fail(self, task_id: int, failure: TaskFailure, now: datetime) -> int | None:
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None or task["status"] == "done":
                return None
            message = f"{failure.reason} {failure.status or ''} {failure.message or ''}".strip()[:300]
            if failure.reason == "blocked":
                self._set_pause(conn, now + timedelta(hours=self.settings.blocked_pause_hours))
                conn.execute("UPDATE tasks SET status = 'pending', leased_at = NULL WHERE status = 'leased'")
                conn.execute("UPDATE tasks SET note = ? WHERE id = ?", (message, task_id))
                return None
            if failure.reason == "retry":
                conn.execute("UPDATE tasks SET status = 'pending', leased_at = NULL WHERE id = ?", (task_id,))
                return None
            if failure.reason == "gone":
                conn.execute(
                    "UPDATE tasks SET status = 'failed', done_at = ?, note = ? WHERE id = ?",
                    (_iso(now), message, task_id),
                )
                if task["ad_id"] is not None:
                    conn.execute("UPDATE ads SET status = 'removed' WHERE id = ?", (task["ad_id"],))
            else:
                attempts = task["attempts"] + 1
                status = "failed" if attempts >= 2 else "pending"
                conn.execute(
                    "UPDATE tasks SET status = ?, attempts = ?, leased_at = NULL, note = ? WHERE id = ?",
                    (status, attempts, message, task_id),
                )
            return self._finish_if_done(conn, task["run_id"], now)

    def _finish_if_done(self, conn: sqlite3.Connection, run_id: int | None, now: datetime) -> int | None:
        if run_id is None:
            return None
        open_tasks = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE run_id = ? AND status IN ('pending', 'leased')", (run_id,)
        ).fetchone()[0]
        if open_tasks:
            return None
        updated = conn.execute(
            "UPDATE runs SET status = 'done', finished_at = ? WHERE id = ? AND status = 'running'",
            (_iso(now), run_id),
        ).rowcount
        if not updated:
            return None
        cutoff = _iso(now - timedelta(days=self.settings.capture_retention_days))
        conn.execute("UPDATE captures SET payload = '{}' WHERE received_at < ? AND payload != '{}'", (cutoff,))
        return run_id

    # ---- status -----------------------------------------------------------------------------------

    def status(self, now: datetime) -> WorkStatus:
        with self.store.connect() as conn:
            paused = self._prepare(conn, now)
            pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('pending', 'leased')").fetchone()[0]
            run = self._progress(conn, self._latest_run_id(conn))
            last_full = self._last_full_start(conn)
            planned = self._details_planned_today(conn, now)
        next_full = last_full + timedelta(days=self.settings.crawl_interval_days) if last_full else None
        return WorkStatus(
            has_work=paused is None and pending > 0,
            paused_until=paused,
            run=run,
            next_full_run=next_full,
            details_today=planned,
            details_limit=self.settings.details_daily_limit,
        )

    def _latest_run_id(self, conn: sqlite3.Connection) -> int | None:
        row = conn.execute(
            "SELECT id FROM runs ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, id DESC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None

    def _progress(self, conn: sqlite3.Connection, run_id: int | None) -> RunProgress | None:
        if run_id is None:
            return None
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        counts = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(status = 'done') AS done,
                      SUM(status = 'failed') AS failed
               FROM tasks WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        ads_seen = conn.execute("SELECT COUNT(*) FROM ads WHERE last_seen >= ?", (run["started_at"],)).fetchone()[0]
        return RunProgress(
            id=run["id"],
            kind=run["kind"],
            status=run["status"],
            started_at=datetime.fromisoformat(run["started_at"]),
            finished_at=datetime.fromisoformat(run["finished_at"]) if run["finished_at"] else None,
            tasks_total=counts["total"] or 0,
            tasks_done=counts["done"] or 0,
            tasks_failed=counts["failed"] or 0,
            ads_seen=ads_seen,
            note=run["note"],
        )

    def runs(self, limit: int = 20) -> list[RunProgress]:
        with self.store.connect() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT ?", (limit,))]
            return [p for p in (self._progress(conn, i) for i in ids) if p is not None]

    def cancel_run(self, run_id: int, now: datetime) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'running'",
                (_iso(now), run_id),
            )
            conn.execute(
                "UPDATE tasks SET status = 'failed', note = 'otkazano' "
                "WHERE run_id = ? AND status IN ('pending', 'leased')",
                (run_id,),
            )
