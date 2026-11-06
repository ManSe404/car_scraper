"""Formatting helpers for the UI and notifications (Serbian number and date style)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def thousands(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{round(value):,}".replace(",", ".")


def money(value: int | float | None, currency: str | None = "€") -> str:
    if value is None:
        return "—"
    return f"{thousands(value)} {currency or '€'}"


def signed_money(value: int | float | None) -> str:
    if not value:
        return ""
    sign = "+" if value > 0 else "−"
    return f"{sign}{thousands(abs(value))} €"


def km(value: int | None) -> str:
    return "—" if value is None else f"{thousands(value)} km"


def to_local(value: datetime | str | None, tz: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = parse_site_or_iso(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz))


def parse_site_or_iso(value: str) -> datetime:
    """Parse our ISO timestamps and the site's 'YYYY-MM-DD HH:MM:SS' (Belgrade local time)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Belgrade"))
    return parsed


def date(value: datetime | str | None, tz: str) -> str:
    local = to_local(value, tz)
    return local.strftime("%d.%m.%Y") if local else "—"


def date_time(value: datetime | str | None, tz: str) -> str:
    local = to_local(value, tz)
    return local.strftime("%d.%m.%Y %H:%M") if local else "—"


def days_since(value: datetime | str | None, now: datetime | None = None) -> int | None:
    if value is None or value == "":
        return None
    moment = parse_site_or_iso(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, ((now or datetime.now(UTC)) - moment).days)
