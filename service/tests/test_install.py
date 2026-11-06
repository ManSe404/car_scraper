from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from carwatch import install

EXTENSION = Path(__file__).resolve().parents[2] / "extension"


def test_extension_id_matches_the_key_in_the_extension() -> None:
    config = (EXTENSION / "wxt.config.ts").read_text(encoding="utf-8")
    key = re.search(r"key:\s*'([A-Za-z0-9+/=]+)'", config)
    assert key is not None, "wxt.config.ts must pin the extension id with a manifest key"
    assert install.extension_id(key.group(1)) == install.EXTENSION_ID


def test_host_name_matches_the_extension() -> None:
    client = (EXTENSION / "lib" / "service.ts").read_text(encoding="utf-8")
    name = re.search(r"const HOST_NAME = '([a-z0-9_.]+)'", client)
    assert name is not None and name.group(1) == install.HOST_NAME


def test_install_writes_a_manifest_for_each_browser(tmp_path: Path) -> None:
    (tmp_path / "google-chrome").mkdir()
    (tmp_path / "chromium").mkdir()
    program = tmp_path / "carwatch-host"
    program.touch()

    written = install.install(base=tmp_path, program=program)

    assert [t.browser for t in written] == ["Google Chrome", "Chromium"]
    manifest = json.loads(written[0].path.read_text(encoding="utf-8"))
    assert manifest == {
        "name": install.HOST_NAME,
        "description": manifest["description"],
        "path": str(program),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{install.EXTENSION_ID}/"],
    }
    assert written[0].path == tmp_path / "google-chrome" / "NativeMessagingHosts" / f"{install.HOST_NAME}.json"
    assert install.installed(tmp_path) == written

    assert install.uninstall(tmp_path) == written
    assert install.installed(tmp_path) == []


def test_without_a_browser_profile_chrome_is_the_target(tmp_path: Path) -> None:
    program = tmp_path / "carwatch-host"
    program.touch()
    assert [t.browser for t in install.install(base=tmp_path, program=program)] == ["Google Chrome"]


def test_install_needs_the_host_program(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        install.install(base=tmp_path, program=tmp_path / "missing")
