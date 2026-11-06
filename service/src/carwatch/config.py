"""Settings for Car Watch.

Values come from environment variables prefixed with ``CARWATCH_``. The native messaging host is started by Chrome, so
it sees Chrome's environment, not the one of your shell; without variables the defaults below apply.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARWATCH_", frozen=True)

    # The web UI (`carwatch ui`) listens here.
    host: str = "127.0.0.1"
    port: int = 8050
    db_path: Path = _data_home() / "carwatch" / "carwatch.db"
    # Host headers the UI answers to. Anything else is rejected, which blocks DNS-rebinding pages.
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    notify: bool = True
    timezone: str = "Europe/Belgrade"

    # ---- collection (the extension's worker tab asks the host for work) ----
    # A full pass over every passenger-car ad on the site starts when the last one is this old.
    crawl_interval_days: int = 7
    # Starting position of the automatic collection switch (automatic full passes and detail pages). Off, only the
    # searches and passes you start yourself run. Once the switch in the extension popup is used, its stored value wins.
    auto_full_runs: bool = True
    # Ad pages (damage, registration, origin, equipment) fetched per day, one request each.
    details_daily_limit: int = 600
    # Optional budget for which ads get details first; empty means every ad, best deals first.
    details_price_max: int | None = None
    details_year_min: int | None = None
    # Pause between two requests to the site, plus up to one second of jitter in the extension.
    request_gap_seconds: float = 3.0
    # The site stops paging deep result lists, so a search is split into price bands of at most this many ads.
    partition_cap: int = 9500
    # A task handed to the extension and not reported back within this time is handed out again.
    lease_seconds: int = 600
    # When the site refuses requests (403/429/503 or a challenge page), all collection waits this long.
    blocked_pause_hours: int = 12
    # Raw capture payloads older than this are emptied (the parsed ads and sightings stay).
    capture_retention_days: int = 30
