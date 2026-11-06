from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from carwatch.app import create_app
from carwatch.config import Settings
from carwatch.host import Host
from carwatch.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    """Contract fixtures written by the extension's tests (extension/tests/extract.test.ts)."""
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return copy.deepcopy(data)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "carwatch.db",
        allowed_hosts=("testserver", "127.0.0.1", "localhost"),
        notify=False,
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def host(settings: Settings) -> Host:
    return Host(settings)
