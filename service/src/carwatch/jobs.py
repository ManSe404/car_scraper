"""Work that outlives one host message: recomputing the fair prices after a full pass.

Chrome starts a new host process for every message and takes its first reply, so the host cannot keep working after
it answers. It starts ``carwatch revalue`` as a detached process instead. A lock file next to the database makes sure
only one valuation runs at a time, whether the host or the web UI asked for it.
"""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
from pathlib import Path

from carwatch.config import Settings

log = logging.getLogger(__name__)


def start_revalue(settings: Settings) -> None:
    """Recompute the fair prices in a separate process that keeps running after the caller exits."""
    env = {**os.environ, "CARWATCH_DB_PATH": str(settings.db_path)}
    try:
        subprocess.Popen(
            [sys.executable, "-m", "carwatch.cli", "revalue"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    except OSError:
        log.exception("could not start the valuation")


def _lock_path(settings: Settings) -> Path:
    return settings.db_path.with_name(settings.db_path.name + ".valuation.lock")


def revalue_running(settings: Settings) -> bool:
    """True while some process is recomputing the fair prices."""
    path = _lock_path(settings)
    if not path.exists():
        return False
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def revalue(settings: Settings) -> bool:
    """Recompute the fair prices now; False when another valuation already holds the lock."""
    lock_path = _lock_path(settings)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        # Imported here: scikit-learn takes about a second to load and the host must answer fast.
        from carwatch import analysis
        from carwatch.store import Store

        try:
            analysis.run_valuation(Store(settings.db_path))
        except Exception:
            log.exception("valuation failed")
        return True
