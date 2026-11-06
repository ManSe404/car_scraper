from __future__ import annotations

from fastapi.testclient import TestClient

from carwatch import analysis
from carwatch.host import Host
from carwatch.store import Store
from tests.test_analysis import NOW, synthetic_market

ORIGIN = {"Origin": "http://testserver"}


def test_runs_page_shows_the_automatic_switch(client: TestClient, host: Host) -> None:
    assert "Automatsko</dt><dd>uključeno" in client.get("/runs").text
    host.crawler.set_auto(False)
    assert "isključeno (uključuje se u ekstenziji)" in client.get("/runs").text


def test_ui_buttons_only_accept_posts_from_the_ui(client: TestClient) -> None:
    assert client.post("/runs/pause").status_code == 403
    assert client.post("/runs/pause", headers={"Origin": "https://evil.example"}).status_code == 403
    response = client.post("/runs/pause", headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert "pauzirano do" in client.get("/runs").text
    client.post("/runs/resume", headers=ORIGIN)
    assert "pauzirano do" not in client.get("/runs").text


def test_analysis_pages_with_data(client: TestClient, store: Store) -> None:
    synthetic_market(store)
    analysis.run_valuation(store, NOW)

    market = client.get("/market")
    assert market.status_code == 200
    assert "Nissan Juke" in market.text and "Volkswagen Golf" in market.text

    compare = client.get("/compare", params={"m": ["Nissan|Juke", "Mazda|3"], "budget": "8000"})
    assert compare.status_code == 200
    assert compare.text.count("<svg") == 2
    assert "za 8.000 €" in compare.text

    many = client.get("/compare", params={"m": ["Nissan|Juke", "Mazda|3", "Volkswagen|Golf", "X|Y"]})
    assert "najviše 3 modela" in many.text

    deals = client.get("/deals")
    assert deals.status_code == 200
    assert "/ads/999999" in deals.text
    assert "tipična greška procene" in deals.text.lower()

    runs = client.get("/runs")
    assert runs.status_code == 200
    assert "Pokreni ceo sajt sada" in runs.text


def test_analysis_pages_without_data(client: TestClient) -> None:
    assert "Još nema podataka" in client.get("/market").text
    assert "štikliraj" in client.get("/compare").text
    assert "još nije izračunata" in client.get("/deals").text
