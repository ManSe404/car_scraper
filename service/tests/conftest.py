from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from carwatch.app import create_app
from carwatch.config import Settings
from carwatch.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test-token"


def load(name: str) -> dict[str, Any]:
    """Contract fixtures written by the extension's tests (extension/tests/extract.test.ts)."""
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return copy.deepcopy(data)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "carwatch.db",
        token=SecretStr(TOKEN),
        token_file=tmp_path / "token",
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
def auth() -> dict[str, str]:
    return {"X-Carwatch-Token": TOKEN}
