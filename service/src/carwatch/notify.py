"""Desktop notifications for price drops (best effort, Linux notify-send)."""

from __future__ import annotations

import logging
import shutil
import subprocess

from carwatch import fmt
from carwatch.models import PriceChange

log = logging.getLogger(__name__)

MAX_NOTIFICATIONS = 5


def notify_price_drops(changes: list[PriceChange]) -> None:
    drops = [c for c in changes if c.delta < 0][:MAX_NOTIFICATIONS]
    exe = shutil.which("notify-send")
    if not drops or exe is None:
        return
    for change in drops:
        prices = f"{fmt.money(change.old_price)} → {fmt.money(change.new_price)}"
        body = f"{prices} ({fmt.signed_money(change.delta)})"
        try:
            subprocess.run(
                [exe, "--app-name=Car Watch", f"Pala cena: {change.title}", body],
                timeout=5,
                check=False,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("notify-send failed for ad %s: %s", change.ad_id, exc)
