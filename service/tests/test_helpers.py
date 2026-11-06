from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from carwatch import fmt, notify
from carwatch.charts import price_chart_svg
from carwatch.models import PriceChange

T0 = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


def test_money_and_km_use_serbian_grouping() -> None:
    assert fmt.money(6399) == "6.399 €"
    assert fmt.money(None) == "—"
    assert fmt.km(188266) == "188.266 km"
    assert fmt.signed_money(-400) == "−400 €"
    assert fmt.signed_money(0) == ""


def test_dates_are_shown_in_belgrade_time() -> None:
    assert fmt.date_time("2026-11-04T23:30:00+00:00", "Europe/Belgrade") == "05.11.2026 00:30"
    assert fmt.date("2026-10-07 12:42:40", "Europe/Belgrade") == "07.10.2026"
    assert fmt.days_since("2026-10-07 12:42:40", now=datetime(2026, 11, 4, 12, tzinfo=UTC)) == 28


def test_chart_draws_one_dot_per_sighting() -> None:
    svg = price_chart_svg([(T0, 6399), (T0 + timedelta(days=2), 5999)], end=T0 + timedelta(days=5), tz="UTC")
    assert svg.startswith("<svg")
    assert svg.count("<circle") == 2
    assert "6.399 €" in svg and "5.999 €" in svg
    assert price_chart_svg([], end=T0, tz="UTC") == ""


def test_notifications_only_for_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr(notify.subprocess, "run", lambda args, **_: calls.append(args))

    notify.notify_price_drops(
        [
            PriceChange(ad_id=1, title="Pao", old_price=7000, new_price=6500),
            PriceChange(ad_id=2, title="Porastao", old_price=6000, new_price=6200),
        ]
    )

    assert len(calls) == 1
    assert calls[0][2] == "Pala cena: Pao"
    assert calls[0][3] == "7.000 € → 6.500 € (−500 €)"


def test_notifications_skip_without_notify_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: pytest.fail("should not run"))
    notify.notify_price_drops([PriceChange(ad_id=1, title="x", old_price=2, new_price=1)])
