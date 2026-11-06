from __future__ import annotations

from fastapi.testclient import TestClient

from carwatch import analysis
from carwatch.store import Store
from tests.test_analysis import NOW, synthetic_market

ORIGIN = {"Origin": "http://testserver"}


def test_work_api_requires_the_token(client: TestClient) -> None:
    assert client.get("/api/work").status_code == 401
    assert client.get("/api/work/status").status_code == 401


def test_work_api_hands_out_tasks_and_takes_failures(client: TestClient, auth: dict[str, str]) -> None:
    status = client.get("/api/work/status", headers=auth).json()
    assert status["has_work"] is True

    batch = client.get("/api/work", params={"limit": 2}, headers=auth).json()
    assert len(batch["tasks"]) == 2
    task = batch["tasks"][0]
    assert task["kind"] == "search" and task["params"]["page"] == "1"

    response = client.post(f"/api/work/{task['id']}/fail", json={"reason": "blocked", "status": 403}, headers=auth)
    assert response.json() == {"ok": True}
    assert client.get("/api/work/status", headers=auth).json()["paused_until"] is not None

    client.post("/api/work/pause", json={"hours": 0}, headers=auth)
    assert client.get("/api/work/status", headers=auth).json()["paused_until"] is None


def test_capture_with_a_task_completes_it(client: TestClient, auth: dict[str, str]) -> None:
    from tests.conftest import load

    task = client.get("/api/work", params={"limit": 1}, headers=auth).json()["tasks"][0]
    capture = load("capture_search.json")
    capture["task_id"] = task["id"]
    capture["search"]["total_items"] = 2
    assert client.post("/api/captures", json=capture, headers=auth).status_code == 200

    ids = {t["id"] for t in client.get("/api/work", params={"limit": 50}, headers=auth).json()["tasks"]}
    assert task["id"] not in ids


def test_search_run_from_the_extension(client: TestClient, auth: dict[str, str]) -> None:
    url = "https://www.polovniautomobili.com/auto-oglasi/pretraga?brand=Nissan&model%5B%5D=Juke&page=3"
    run = client.post("/api/runs", json={"kind": "search", "page_url": url}, headers=auth).json()
    assert run["run_id"] > 0
    assert client.post("/api/runs", json={"kind": "search"}, headers=auth).status_code == 422


def test_automatic_switch_from_the_extension(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/api/work/auto", json={"enabled": False}).status_code == 401

    assert client.post("/api/work/auto", json={"enabled": False}, headers=auth).json() == {"ok": True}
    status = client.get("/api/work/status", headers=auth).json()
    assert status["auto"] is False and status["has_work"] is False
    assert "isključeno (uključuje se u ekstenziji)" in client.get("/runs").text

    client.post("/api/work/auto", json={"enabled": True}, headers=auth)
    status = client.get("/api/work/status", headers=auth).json()
    assert status["auto"] is True and status["has_work"] is True
    assert status["run"]["auto"] is True


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
