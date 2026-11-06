from __future__ import annotations

from fastapi.testclient import TestClient

from carwatch.host import Host
from tests.conftest import load


def capture(host: Host, data: dict[str, object]) -> None:
    reply = host.handle({"type": "capture", "capture": data})
    assert reply["ok"] is True, reply


def test_foreign_host_header_is_rejected(client: TestClient) -> None:
    # A DNS-rebinding page would reach 127.0.0.1 with its own host name.
    response = client.get("/", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_ui_pages(client: TestClient, host: Host) -> None:
    assert "Još nema oglasa" in client.get("/").text

    capture(host, load("capture_search.json"))
    capture(host, load("capture_ad.json"))

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


def test_ui_escapes_ad_text(client: TestClient, host: Host) -> None:
    data = load("capture_search.json")
    data["items"][0]["title"] = "<script>alert(1)</script>"
    capture(host, data)

    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
