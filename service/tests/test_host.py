from __future__ import annotations

import fcntl
import io
import json
import os
import sqlite3
import struct
import subprocess
import sys
from typing import Any

import pytest

from carwatch import host as host_module
from carwatch import jobs
from carwatch.config import Settings
from carwatch.host import MAX_REPLY_BYTES, Host, ProtocolError, encode_message, read_message
from tests.conftest import load

ORIGIN = "chrome-extension://anfomkmnpkpjpjdlpngmmkedbplleiam/"


def ask(host: Host, message: Any) -> Any:
    reply = host.handle(message)
    assert reply["ok"] is True, reply
    return reply["value"]


def run_program(settings: Settings, stdin: bytes) -> bytes:
    """Start the host the way Chrome does: a fresh process, the origin as its argument, one message on stdin."""
    env = {**os.environ, "CARWATCH_DB_PATH": str(settings.db_path), "CARWATCH_NOTIFY": "false"}
    result = subprocess.run(
        [sys.executable, "-m", "carwatch.host", ORIGIN],
        input=stdin,
        capture_output=True,
        timeout=60,
        env=env,
        check=True,
    )
    return result.stdout


# ---- protocol ------------------------------------------------------------------------------------------


def test_message_framing_round_trip() -> None:
    framed = encode_message({"type": "status", "text": "čćž"})
    assert struct.unpack("=I", framed[:4])[0] == len(framed) - 4
    assert read_message(io.BytesIO(framed)) == {"type": "status", "text": "čćž"}
    assert read_message(io.BytesIO(b"")) is None


def test_cut_short_messages_are_refused() -> None:
    with pytest.raises(ProtocolError):
        read_message(io.BytesIO(b"\x05\x00"))
    with pytest.raises(ProtocolError):
        read_message(io.BytesIO(struct.pack("=I", 10) + b"{}"))


def test_a_reply_over_chromes_limit_becomes_an_error() -> None:
    framed = encode_message({"blob": "x" * (MAX_REPLY_BYTES + 1)})
    reply = json.loads(framed[4:])
    assert reply["ok"] is False and "prevelik" in reply["error"]


def test_the_program_answers_one_framed_message(settings: Settings) -> None:
    stdout = run_program(settings, encode_message({"type": "capture", "capture": load("capture_search.json")}))

    reply = read_message(io.BytesIO(stdout))
    assert reply["ok"] is True and reply["value"]["new_ads"] == 2
    # Chrome reads stdout as messages only: nothing may follow the one reply.
    assert len(stdout) == 4 + struct.unpack("=I", stdout[:4])[0]


def test_the_program_answers_garbage_with_an_error(settings: Settings) -> None:
    reply = read_message(io.BytesIO(run_program(settings, struct.pack("=I", 3) + b"abc")))
    assert reply["ok"] is False and "neispravna poruka" in reply["error"]


# ---- requests ------------------------------------------------------------------------------------------


def test_status_reports_the_database_and_the_work(host: Host) -> None:
    value = ask(host, {"type": "status"})
    assert (value["ads"], value["captures"]) == (0, 0)
    assert value["work"]["auto"] is True and value["work"]["has_work"] is True


def test_search_capture_round_trip(host: Host) -> None:
    value = ask(host, {"type": "capture", "capture": load("capture_search.json")})
    assert (value["ads_seen"], value["new_ads"], value["summary"]) == (2, 2, None)
    status = ask(host, {"type": "status"})
    assert (status["ads"], status["captures"]) == (2, 1)


def test_ad_capture_returns_the_badge_summary(host: Host) -> None:
    summary = ask(host, {"type": "capture", "capture": load("capture_ad.json")})["summary"]
    assert summary["known"] is True
    assert summary["price"] == 6690
    assert summary["published_at"] == "2026-10-07 12:42:40"
    assert summary["ui_url"] is None


def test_unexpected_fields_are_not_stored(host: Host, settings: Settings) -> None:
    capture = load("capture_search.json")
    capture["items"][0]["sellerName"] = "Petar Petrović"
    capture["sellerPhone"] = "064 000 0000"
    ask(host, {"type": "capture", "capture": capture})

    raw = b"".join(path.read_bytes() for path in settings.db_path.parent.glob(settings.db_path.name + "*"))
    assert b"Petar" not in raw
    assert b"064 000" not in raw


@pytest.mark.parametrize(
    "message",
    [
        {"type": "capture", "capture": {"kind": "search"}},
        {"type": "unknown"},
        {"type": "status", "extra": 1},
        "not an object",
    ],
)
def test_invalid_messages_are_refused(host: Host, message: Any) -> None:
    reply = host.handle(message)
    assert reply["ok"] is False and reply["error"].startswith("neispravna poruka")
    assert reply["retry"] is False


def test_a_failing_database_is_worth_retrying(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(*_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(host.store, "ingest", locked)
    reply = host.handle({"type": "capture", "capture": load("capture_search.json")})
    assert reply == {"ok": False, "error": "OperationalError: database is locked", "retry": True}


def test_work_is_handed_out_and_a_refusal_pauses_it(host: Host) -> None:
    batch = ask(host, {"type": "work-next", "limit": 2})
    assert len(batch["tasks"]) == 2
    task = batch["tasks"][0]
    assert task["kind"] == "search" and task["params"]["page"] == "1"

    failure = {"reason": "blocked", "status": 403}
    assert ask(host, {"type": "work-fail", "task_id": task["id"], "failure": failure}) == {"ok": True}
    assert ask(host, {"type": "status"})["work"]["paused_until"] is not None

    ask(host, {"type": "pause", "hours": 0})
    assert ask(host, {"type": "status"})["work"]["paused_until"] is None


def test_capture_with_a_task_completes_it(host: Host) -> None:
    task = ask(host, {"type": "work-next", "limit": 1})["tasks"][0]
    capture = load("capture_search.json")
    capture["task_id"] = task["id"]
    capture["search"]["total_items"] = 2
    ask(host, {"type": "capture", "capture": capture})

    assert task["id"] not in {t["id"] for t in ask(host, {"type": "work-next", "limit": 50})["tasks"]}


def test_search_run_from_the_extension(host: Host) -> None:
    url = "https://www.polovniautomobili.com/auto-oglasi/pretraga?brand=Nissan&model%5B%5D=Juke&page=3"
    assert ask(host, {"type": "start-run", "kind": "search", "page_url": url})["run_id"] > 0

    reply = host.handle({"type": "start-run", "kind": "search"})
    assert reply["ok"] is False and "page_url" in reply["error"]


def test_automatic_switch_from_the_extension(host: Host) -> None:
    ask(host, {"type": "auto", "enabled": False})
    work = ask(host, {"type": "status"})["work"]
    assert work["auto"] is False and work["has_work"] is False

    ask(host, {"type": "auto", "enabled": True})
    work = ask(host, {"type": "status"})["work"]
    assert work["auto"] is True and work["has_work"] is True
    assert work["run"]["auto"] is True


def test_a_finished_run_starts_the_valuation(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Settings] = []
    monkeypatch.setattr(host_module, "start_revalue", started.append)
    ask(host, {"type": "auto", "enabled": False})
    url = "https://www.polovniautomobili.com/auto-oglasi/pretraga?brand=Nissan"
    ask(host, {"type": "start-run", "kind": "search", "page_url": url})

    task = ask(host, {"type": "work-next", "limit": 1})["tasks"][0]
    capture = load("capture_search.json")
    capture["task_id"] = task["id"]
    capture["search"]["total_items"] = 2
    ask(host, {"type": "capture", "capture": capture})

    assert started == [host.settings]


# ---- valuation lock ------------------------------------------------------------------------------------


def test_only_one_valuation_runs_at_a_time(settings: Settings) -> None:
    lock_path = settings.db_path.with_name(settings.db_path.name + ".valuation.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert jobs.revalue_running(settings) is True
        assert jobs.revalue(settings) is False
    assert jobs.revalue_running(settings) is False
