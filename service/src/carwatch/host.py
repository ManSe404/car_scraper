"""Native messaging host: the extension's only way into Car Watch.

Chrome starts this program for every message the extension sends with ``runtime.sendNativeMessage``: one JSON message
on stdin, one reply on stdout, each preceded by its length as a 32-bit unsigned integer in native byte order. Chrome
passes the caller's origin as the first argument and lets only the extension named in the host manifest call us (see
``carwatch.install``). Nothing else may be written to stdout; logs go to stderr, which Chrome forwards to its own.

https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
"""

from __future__ import annotations

import json
import logging
import struct
import sys
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, BinaryIO, Literal
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from carwatch import __version__
from carwatch.config import Settings
from carwatch.crawl import Crawler
from carwatch.jobs import start_revalue
from carwatch.models import Capture, TaskFailure
from carwatch.notify import notify_price_drops
from carwatch.store import Store

log = logging.getLogger(__name__)

_LENGTH = struct.Struct("=I")  # native byte order, standard 4-byte size
# Chrome refuses replies over 1 MB and sends us at most 64 MiB.
MAX_REPLY_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024 * 1024


class ProtocolError(Exception):
    """The bytes on stdin are not a native messaging message."""


def read_message(stream: BinaryIO) -> Any:
    """Read one message; None when the stream is already at its end."""
    header = stream.read(_LENGTH.size)
    if not header:
        return None
    if len(header) < _LENGTH.size:
        raise ProtocolError("message length is cut short")
    (length,) = _LENGTH.unpack(header)
    if length > MAX_REQUEST_BYTES:
        raise ProtocolError(f"message of {length} bytes is over the limit")
    body = stream.read(length)
    if len(body) < length:
        raise ProtocolError("message body is cut short")
    return json.loads(body)


def encode_message(message: Any) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_REPLY_BYTES:
        body = json.dumps({"ok": False, "error": f"odgovor od {len(body)} bajtova je prevelik"}).encode("utf-8")
    return _LENGTH.pack(len(body)) + body


def write_message(stream: BinaryIO, message: Any) -> None:
    stream.write(encode_message(message))
    stream.flush()


# ---- requests (extension/lib/messages.ts: HostRequest) --------------------------------------------------


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusRequest(_Request):
    type: Literal["status"]


class CaptureRequest(_Request):
    type: Literal["capture"]
    capture: Capture


class WorkNextRequest(_Request):
    type: Literal["work-next"]
    limit: int = 10


class WorkFailRequest(_Request):
    type: Literal["work-fail"]
    task_id: int
    failure: TaskFailure


class StartRunRequest(_Request):
    type: Literal["start-run"]
    kind: Literal["full", "search"] = "full"
    page_url: str | None = None


class PauseRequest(_Request):
    type: Literal["pause"]
    hours: float = 24


class AutoRequest(_Request):
    type: Literal["auto"]
    enabled: bool


HostRequest = Annotated[
    StatusRequest | CaptureRequest | WorkNextRequest | WorkFailRequest | StartRunRequest | PauseRequest | AutoRequest,
    Field(discriminator="type"),
]
_requests: TypeAdapter[HostRequest] = TypeAdapter(HostRequest)


class Host:
    """Answers one request.

    Replies are ``{"ok": true, "value": ...}`` or ``{"ok": false, "error": "...", "retry": bool}``. ``retry`` is
    true when the request was fine but Car Watch failed (a locked or unreadable database), so the extension keeps a
    capture in its outbox instead of dropping it.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.crawler = Crawler(self.store, settings)

    def handle(self, raw: Any, now: datetime | None = None) -> dict[str, Any]:
        try:
            request = _requests.validate_python(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            return {"ok": False, "error": f"neispravna poruka ({where}: {first['msg']})", "retry": False}
        try:
            value = self._dispatch(request, now or datetime.now(UTC))
        except Exception as exc:
            log.exception("request %s failed", request.type)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "retry": True}
        return {"ok": True, "value": value}

    def _dispatch(self, request: HostRequest, now: datetime) -> Any:
        crawler = self.crawler
        match request:
            case StatusRequest():
                stats = self.store.stats()
                work = crawler.status(now)
                return {
                    "version": __version__,
                    "ads": stats["ads"],
                    "captures": stats["captures"],
                    "work": work.model_dump(mode="json"),
                }
            case CaptureRequest(capture=capture):
                return self._capture(capture, now)
            case WorkNextRequest(limit=limit):
                return crawler.lease(now, max(1, min(limit, 50))).model_dump(mode="json")
            case WorkFailRequest(task_id=task_id, failure=failure):
                if failure.reason == "blocked":
                    log.warning("site refused a request (task %s): %s %s", task_id, failure.status, failure.message)
                if crawler.fail(task_id, failure, now) is not None:
                    start_revalue(self.settings)
                return {"ok": True}
            case StartRunRequest(kind="search", page_url=page_url):
                if not page_url:
                    raise ValueError("page_url je obavezan za pretragu")
                return {"run_id": crawler.start_search_run(dict(parse_qsl(urlparse(page_url).query)), now)}
            case StartRunRequest():
                crawler.pause(None)
                return {"run_id": crawler.start_full_run(now)}
            case PauseRequest(hours=hours):
                crawler.pause(now + timedelta(hours=hours) if hours > 0 else None)
                return {"ok": True}
            case AutoRequest(enabled=enabled):
                crawler.set_auto(enabled)
                return {"ok": True}

    def _capture(self, capture: Capture, now: datetime) -> dict[str, Any]:
        result = self.store.ingest(capture)
        if capture.task_id is not None and self.crawler.complete(capture.task_id, capture, now) is not None:
            start_revalue(self.settings)
        if capture.kind == "ad" and capture.ad is not None:
            result.summary = self.store.summary(capture.ad.id)
        if self.settings.notify and result.price_changes:
            notify_price_drops(result.price_changes)
        log.info(
            "capture %s kind=%s ads=%d new=%d price_changes=%d",
            result.capture_id, capture.kind, result.ads_seen, result.new_ads, len(result.price_changes),
        )  # fmt: skip
        return result.model_dump(mode="json")


def main() -> None:
    """Entry point Chrome runs (``carwatch-host``): answer one message, then exit."""
    # Chrome passes our stderr on to its own; keep it to warnings so a day of collection does not flood it.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="carwatch-host %(levelname)s %(message)s")
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    try:
        message = read_message(stdin)
    except (ProtocolError, ValueError) as exc:
        write_message(stdout, {"ok": False, "error": f"neispravna poruka: {exc}", "retry": False})
        return
    if message is None:
        return
    try:
        reply = Host(Settings()).handle(message)
    except Exception as exc:  # the database could not be opened, or the settings are invalid
        log.exception("host failed to start")
        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "retry": True}
    write_message(stdout, reply)


if __name__ == "__main__":
    main()
