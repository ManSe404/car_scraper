from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from carwatch.config import Settings
from carwatch.crawl import FULL_BANDS, Crawler, split_band
from carwatch.models import Capture, TaskFailure
from carwatch.store import SCHEMA_VERSION, Store
from tests.conftest import load

T0 = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)


@pytest.fixture
def crawl_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "crawl.db",
        token=SecretStr("t"),
        token_file=tmp_path / "token",
        partition_cap=100,
        details_daily_limit=5,
        request_gap_seconds=0,
        notify=False,
    )


@pytest.fixture
def crawler(crawl_settings: Settings) -> Crawler:
    return Crawler(Store(crawl_settings.db_path), crawl_settings)


def search_capture(task_id: int, total: int, page: int = 1, at: datetime = T0, first_id: int = 1) -> Capture:
    data = load("capture_search.json")
    data["task_id"] = task_id
    data["captured_at"] = at.isoformat()
    data["search"]["total_items"] = total
    data["search"]["page"] = page
    for offset, item in enumerate(data["items"]):
        item["id"] = first_id + offset
        item["url"] = f"https://www.polovniautomobili.com/auto-oglasi/{first_id + offset}/x"
    return Capture.model_validate(data)


def pending_search(crawler: Crawler) -> int:
    with crawler.store.connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE kind = 'search' AND status IN ('pending', 'leased')"
            ).fetchone()[0]
        )


def finish_everything(crawler: Crawler, now: datetime, total: int = 10) -> None:
    """Answer every search task with a small result so the run completes."""
    crawler.status(now)
    next_id = 1000
    while pending_search(crawler):
        for task in crawler.lease(now, 50).tasks:
            assert task.kind == "search"
            crawler.complete(task.id, search_capture(task.id, total, first_id=next_id, at=now), now)
            crawler.store.ingest(search_capture(task.id, total, first_id=next_id, at=now))
            next_id += 2


def test_first_status_starts_a_full_pass_in_price_bands(crawler: Crawler) -> None:
    status = crawler.status(T0)

    assert status.has_work
    assert status.run is not None and status.run.kind == "full"
    assert status.run.tasks_total == len(FULL_BANDS)
    batch = crawler.lease(T0, 3)
    assert [t.kind for t in batch.tasks] == ["search"] * 3
    assert batch.tasks[0].path == "/auto-oglasi/pretraga"
    assert batch.tasks[0].params == {"showOldNew": "all", "priceFrom": "0", "priceTo": "999", "page": "1"}


def test_leased_tasks_are_not_handed_out_twice_until_the_lease_expires(crawler: Crawler) -> None:
    first = {t.id for t in crawler.lease(T0, 50).tasks}
    assert crawler.lease(T0 + timedelta(minutes=1), 50).tasks == []
    again = {t.id for t in crawler.lease(T0 + timedelta(minutes=11), 50).tasks}
    assert again == first


def test_page_one_plans_the_remaining_pages(crawler: Crawler) -> None:
    task = crawler.lease(T0, 1).tasks[0]
    crawler.complete(task.id, search_capture(task.id, total=60), T0)

    pages = [t for t in crawler.lease(T0, 50).tasks if t.params.get("priceFrom") == "0"]
    assert sorted(int(t.params["page"]) for t in pages) == [2, 3]


def test_a_band_over_the_cap_is_halved(crawler: Crawler) -> None:
    task = crawler.lease(T0, 1).tasks[0]
    crawler.complete(task.id, search_capture(task.id, total=250), T0)

    halves = [t.params for t in crawler.lease(T0, 50).tasks if t.params.get("priceFrom") in {"0", "500"}]
    assert {(h["priceFrom"], h["priceTo"], h["page"]) for h in halves} == {("0", "499", "1"), ("500", "999", "1")}


def test_split_band_rules() -> None:
    assert split_band({"priceFrom": "70000"}) == [
        {"priceFrom": "70000", "priceTo": "139999"},
        {"priceFrom": "140000"},
    ]
    assert split_band({"priceFrom": "5000", "priceTo": "5000"}) is None


def test_blocked_pauses_everything(crawler: Crawler) -> None:
    task = crawler.lease(T0, 1).tasks[0]
    crawler.fail(task.id, TaskFailure(reason="blocked", status=403), T0)

    batch = crawler.lease(T0 + timedelta(hours=1), 10)
    assert batch.tasks == []
    assert batch.paused_until == T0 + timedelta(hours=12)
    assert crawler.status(T0 + timedelta(hours=1)).has_work is False
    assert crawler.lease(T0 + timedelta(hours=13), 1).tasks[0].id == task.id


def test_errors_are_retried_once(crawler: Crawler) -> None:
    task = crawler.lease(T0, 1).tasks[0]
    crawler.fail(task.id, TaskFailure(reason="drift", message="x"), T0)
    again = crawler.lease(T0, 1).tasks[0]
    assert again.id == task.id
    crawler.fail(task.id, TaskFailure(reason="error"), T0)
    assert task.id not in {t.id for t in crawler.lease(T0, 50).tasks}


def test_run_finishes_and_details_follow_within_the_daily_limit(crawler: Crawler) -> None:
    finish_everything(crawler, T0)

    status = crawler.status(T0)
    assert status.run is not None and status.run.status == "done"
    # Right after the pass the fair prices are being recomputed; details wait for them (up to 15 minutes).
    assert crawler.lease(T0, 50).tasks == []
    later = T0 + timedelta(minutes=16)
    details = crawler.lease(later, 50).tasks
    assert len(details) == 5
    assert all(t.kind == "detail" and t.path.startswith("/auto-oglasi/") for t in details)
    assert crawler.lease(later + timedelta(minutes=5), 50).tasks == []
    assert crawler.status(later).details_today == 5
    # The next day brings a new quota (the five left unanswered come back too, their lease expired).
    next_day = crawler.lease(T0 + timedelta(days=1), 50).tasks
    assert len([t for t in next_day if t.id not in {d.id for d in details}]) == 5


def test_gone_ad_is_marked_removed(crawler: Crawler) -> None:
    finish_everything(crawler, T0)
    detail = crawler.lease(T0 + timedelta(minutes=16), 1).tasks[0]
    ad_id = int(detail.path.split("/")[2])
    crawler.fail(detail.id, TaskFailure(reason="gone", status=404), T0)

    ad, _, _ = crawler.store.get_ad(ad_id)
    assert ad is not None and ad["status"] == "removed"


def test_full_pass_repeats_after_the_interval(crawler: Crawler) -> None:
    finish_everything(crawler, T0)
    assert crawler.status(T0 + timedelta(days=3)).run is not None
    assert [r.kind for r in crawler.runs()] == ["full"]
    crawler.status(T0 + timedelta(days=7, minutes=1))
    assert [r.kind for r in crawler.runs()] == ["full", "full"]


def test_search_run_from_a_page_url(crawler: Crawler) -> None:
    crawler.pause(T0 + timedelta(days=30))  # keep the automatic full pass out of the way
    run_id = crawler.start_search_run({"brand": "Nissan", "model[]": "Juke", "page": "4"}, T0)
    crawler.pause(None)

    tasks = [t for t in crawler.lease(T0, 50).tasks if t.params.get("brand") == "Nissan"]
    assert len(tasks) == 1
    assert tasks[0].params == {"brand": "Nissan", "model[]": "Juke", "page": "1"}
    assert any(r.id == run_id and r.kind == "search" for r in crawler.runs())


def test_worker_captures_do_not_fill_the_searches_page(crawler: Crawler) -> None:
    task = crawler.lease(T0, 1).tasks[0]
    crawler.store.ingest(search_capture(task.id, total=10))
    assert crawler.store.list_searches() == []


def test_automatic_full_runs_can_be_turned_off(crawl_settings: Settings) -> None:
    manual = Crawler(Store(crawl_settings.db_path), crawl_settings.model_copy(update={"auto_full_runs": False}))
    assert manual.status(T0).has_work is False
    manual.start_search_run({"brand": "Nissan"}, T0)
    assert [t.params["brand"] for t in manual.lease(T0, 10).tasks] == ["Nissan"]


def test_switch_off_holds_the_automatic_pass_but_not_the_users_searches(crawler: Crawler) -> None:
    status = crawler.status(T0)
    assert status.auto and status.run is not None and status.run.auto
    crawler.set_auto(False)

    status = crawler.status(T0)
    assert status.auto is False and status.has_work is False
    assert crawler.lease(T0, 50).tasks == []

    crawler.start_search_run({"brand": "Nissan"}, T0)
    assert crawler.status(T0).has_work is True
    assert [t.params.get("brand") for t in crawler.lease(T0, 50).tasks] == ["Nissan"]

    # Back on, the automatic pass continues where it stopped: its tasks were never dropped.
    crawler.set_auto(True)
    assert len(crawler.lease(T0, 50).tasks) == len(FULL_BANDS)


def test_switch_off_plans_no_detail_pages(crawler: Crawler) -> None:
    finish_everything(crawler, T0)
    crawler.set_auto(False)

    later = T0 + timedelta(minutes=16)
    assert crawler.lease(later, 50).tasks == []
    assert crawler.status(later).details_today == 0

    crawler.set_auto(True)
    assert len(crawler.lease(later, 50).tasks) == 5


def test_starting_a_full_pass_claims_the_automatic_one(crawler: Crawler) -> None:
    automatic = crawler.status(T0).run
    assert automatic is not None
    crawler.set_auto(False)

    assert crawler.start_full_run(T0) == automatic.id
    assert crawler.runs()[0].auto is False
    assert len(crawler.lease(T0, 50).tasks) == len(FULL_BANDS)


def test_switch_is_stored_and_wins_over_the_setting(crawl_settings: Settings) -> None:
    off = crawl_settings.model_copy(update={"auto_full_runs": False})
    first = Crawler(Store(off.db_path), off)
    assert first.status(T0).auto is False
    first.set_auto(True)

    restarted = Crawler(Store(off.db_path), off)
    status = restarted.status(T0)
    assert status.auto is True and status.has_work is True


def test_old_database_gets_the_auto_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, root_query TEXT NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, note TEXT)"
    )
    old.executemany(
        "INSERT INTO runs (kind, root_query, started_at, status) VALUES (?, '{}', '2026-11-01', ?)",
        [("full", "done"), ("full", "running"), ("search", "running")],
    )
    old.commit()
    old.close()
    store = Store(path)

    with store.connect() as conn:
        rows = conn.execute("SELECT kind, status, auto FROM runs ORDER BY id").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert [(r["kind"], r["status"], r["auto"]) for r in rows] == [
        ("full", "done", 0),
        ("full", "running", 1),
        ("search", "running", 0),
    ]
    assert version == SCHEMA_VERSION
    Store(path)  # opening it again changes nothing
