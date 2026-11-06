from __future__ import annotations

from fastapi.testclient import TestClient

from carwatch.config import Settings
from tests.conftest import load


def test_capture_requires_the_token(client: TestClient) -> None:
    assert client.post("/api/captures", json=load("capture_search.json")).status_code == 401
    bad = {"X-Carwatch-Token": "wrong"}
    assert client.post("/api/captures", json=load("capture_search.json"), headers=bad).status_code == 401


def test_foreign_host_header_is_rejected(client: TestClient, auth: dict[str, str]) -> None:
    # A DNS-rebinding page would reach 127.0.0.1 with its own host name.
    response = client.get("/", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_search_capture_round_trip(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/captures", json=load("capture_search.json"), headers=auth)

    assert response.status_code == 200
    body = response.json()
    assert (body["ads_seen"], body["new_ads"], body["summary"]) == (2, 2, None)
    health = client.get("/api/health", headers=auth).json()
    assert (health["ok"], health["ads"], health["captures"]) == (True, 2, 1)


def test_ad_capture_returns_the_badge_summary(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/captures", json=load("capture_ad.json"), headers=auth)

    summary = response.json()["summary"]
    assert summary["known"] is True
    assert summary["price"] == 6690
    assert summary["published_at"] == "2026-10-07 12:42:40"
    assert summary["ui_url"] == "http://testserver/ads/10000003"


def test_unexpected_fields_are_not_stored(client: TestClient, auth: dict[str, str], settings: Settings) -> None:
    capture = load("capture_search.json")
    capture["items"][0]["sellerName"] = "Petar Petrović"
    capture["sellerPhone"] = "064 000 0000"
    client.post("/api/captures", json=capture, headers=auth)

    raw = b"".join(path.read_bytes() for path in settings.db_path.parent.glob(settings.db_path.name + "*"))
    assert b"Petar" not in raw
    assert b"064 000" not in raw


def test_invalid_capture_is_rejected(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/captures", json={"kind": "search"}, headers=auth)
    assert response.status_code == 422


def test_ui_pages(client: TestClient, auth: dict[str, str]) -> None:
    assert "Još nema oglasa" in client.get("/").text

    client.post("/api/captures", json=load("capture_search.json"), headers=auth)
    client.post("/api/captures", json=load("capture_ad.json"), headers=auth)

    listing = client.get("/")
    assert listing.status_code == 200
    assert "Nissan Juke 1.6 Acenta" in listing.text
    assert "6.399 €" in listing.text

    detail = client.get("/ads/10000003")
    assert detail.status_code == 200
    assert "Nije oštećen" in detail.text
    assert "[telefon]" in detail.text
    assert "od drugog viđenja" in detail.text  # one sighting: no chart yet

    assert client.get("/ads/999").status_code == 404

    searches = client.get("/searches")
    assert searches.status_code == 200
    assert "Nissan Juke" in searches.text
    assert "cena do 8000" in searches.text

    filtered = client.get("/", params={"price_max": "", "q": "Acenta", "sort": "nonsense"})
    assert filtered.status_code == 200
    assert "Nissan Juke 1.6 Acenta" in filtered.text
    assert "Nissan Juke 1.5 dci" not in filtered.text


def test_ui_escapes_ad_text(client: TestClient, auth: dict[str, str]) -> None:
    capture = load("capture_search.json")
    capture["items"][0]["title"] = "<script>alert(1)</script>"
    client.post("/api/captures", json=capture, headers=auth)

    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
