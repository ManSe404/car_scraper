"""Register the native messaging host with Chrome and Chromium on Linux.

Chrome finds a host through a small JSON manifest in its ``NativeMessagingHosts`` folder. The manifest names the
program to start and the one extension allowed to talk to it. The extension's id is fixed by the public ``key`` in
``extension/wxt.config.ts``; ``extension_id`` turns that key into the id, and a test checks the two stay in step.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from carwatch.config import config_home

HOST_NAME = "io.github.manse404.carwatch"
EXTENSION_ID = "anfomkmnpkpjpjdlpngmmkedbplleiam"

# User-level manifest folders, relative to ~/.config (or $XDG_CONFIG_HOME).
BROWSERS: dict[str, str] = {
    "Google Chrome": "google-chrome",
    "Chromium": "chromium",
}


def extension_id(public_key_b64: str) -> str:
    """Chrome's extension id: the first 128 bits of SHA-256 over the DER public key, written with letters a-p."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def host_program() -> Path:
    """The ``carwatch-host`` script next to the Python running this command (the project's virtual environment)."""
    return Path(sys.executable).parent / "carwatch-host"


def manifest(program: Path) -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": "Car Watch: stores the car ads the extension collects",
        "path": str(program),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }


@dataclass(frozen=True)
class Target:
    browser: str
    path: Path


def targets(base: Path | None = None) -> list[Target]:
    """Manifest paths for every supported browser that has a profile folder; Chrome when none has one yet."""
    root = base or config_home()
    found = _candidates(root)
    present = [t for t in found if t.path.parent.parent.is_dir()]
    return present or found[:1]


def _candidates(root: Path) -> list[Target]:
    return [
        Target(name, root / folder / "NativeMessagingHosts" / f"{HOST_NAME}.json") for name, folder in BROWSERS.items()
    ]


def install(base: Path | None = None, program: Path | None = None) -> list[Target]:
    program = program or host_program()
    if not program.is_file():
        raise FileNotFoundError(f"{program} ne postoji; pokreni `uv sync` u service/")
    written = targets(base)
    content = json.dumps(manifest(program), indent=2) + "\n"
    for target in written:
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(content, encoding="utf-8")
    return written


def uninstall(base: Path | None = None) -> list[Target]:
    removed = installed(base)
    for target in removed:
        target.path.unlink()
    return removed


def installed(base: Path | None = None) -> list[Target]:
    return [t for t in _candidates(base or config_home()) if t.path.exists()]
