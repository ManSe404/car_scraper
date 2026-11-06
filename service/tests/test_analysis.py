from __future__ import annotations

import math
import random
from datetime import UTC, datetime

import pytest

from carwatch import analysis
from carwatch.models import Capture, SearchInfo, SearchItem
from carwatch.store import Store

NOW = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
MODELS = {
    ("Nissan", "Juke"): (17000, "Džip/SUV", 85),
    ("Mazda", "3"): (19000, "Limuzina", 88),
    ("Volkswagen", "Golf"): (21000, "Hečbek", 81),
}
UNDERPRICED_ID = 999_999


def synthetic_market(store: Store, per_model: int = 120) -> None:
    """Prices fall about 12% a year and with mileage, with 8% noise; one Golf is listed at half its value."""
    rng = random.Random(7)
    items: list[SearchItem] = []
    next_id = 1
    for (brand, model), (new_price, chassis, kw) in MODELS.items():
        for _ in range(per_model):
            year = rng.randint(2010, 2022)
            age = 2026 - year
            km = int(age * rng.uniform(12_000, 22_000))
            price = new_price * 0.88**age * math.exp(-km / 900_000) * rng.lognormvariate(0, 0.08)
            items.append(_item(next_id, brand, model, year, km, int(price), chassis, kw, rng))
            next_id += 1
    fair = 21000 * 0.88**8 * math.exp(-8 * 17_000 / 900_000)
    items.append(_item(UNDERPRICED_ID, "Volkswagen", "Golf", 2018, 8 * 17_000, int(fair * 0.5), "Hečbek", 81, rng))
    for start in range(0, len(items), 25):
        store.ingest(
            Capture(
                kind="search",
                page_url="https://www.polovniautomobili.com/auto-oglasi/pretraga",
                captured_at=NOW,
                search=SearchInfo(query={}, page=1, total_items=len(items)),
                items=items[start : start + 25],
            )
        )


def _item(
    ad_id: int, brand: str, model: str, year: int, km: int, price: int, chassis: str, kw: int, rng: random.Random
) -> SearchItem:
    return SearchItem(
        id=ad_id,
        title=f"{brand} {model}",
        url=f"https://www.polovniautomobili.com/auto-oglasi/{ad_id}/x",
        brand=brand,
        model=model,
        year=year,
        mileage_km=km,
        fuel=rng.choice(["Dizel", "Benzin"]),
        gearbox=rng.choice(["Manuelni 6 brzina", "Automatski / poluautomatski"]),
        chassis=chassis,
        power_kw=kw,
        engine_cc=1500,
        price=price,
        is_dealer=rng.random() < 0.3,
        status="active",
    )


@pytest.fixture
def market_store(store: Store) -> Store:
    synthetic_market(store)
    return store


def test_market_statistics_per_model(market_store: Store) -> None:
    cars = analysis.load_snapshot(market_store, NOW)
    rows = {(r.brand, r.model): r for r in analysis.market(cars, analysis.Filters())}

    assert set(rows) == set(MODELS)
    juke = rows[("Nissan", "Juke")]
    assert juke.n == 120
    assert juke.loss_per_year is not None and 0.10 < juke.loss_per_year < 0.17
    assert (
        juke.price_p25 is not None and juke.price_p75 is not None and juke.price_p25 < juke.price_med < juke.price_p75
    )


def test_filters_narrow_the_market(market_store: Store) -> None:
    cars = analysis.load_snapshot(market_store, NOW)
    rows = analysis.market(cars, analysis.Filters(year_min=2020, min_count=1))
    assert all((r.year_med or 0) >= 2020 for r in rows)
    assert analysis.market(cars, analysis.Filters(brand="Mazda", min_count=1))[0].brand == "Mazda"


def test_valuation_finds_the_underpriced_car(market_store: Store) -> None:
    result = analysis.run_valuation(market_store, NOW)

    assert result.ads == 361
    assert result.median_error_pct is not None and result.median_error_pct < 0.15
    cars = analysis.load_snapshot(market_store, NOW)
    top = analysis.deals(cars, analysis.Filters(), limit=3)
    assert top[0][0].id == UNDERPRICED_ID
    assert (top[0][0].deal_pct or 0) > 0.3
    meta = analysis.valuation_meta(market_store)
    assert meta is not None and meta["ads"] == 361


def test_valuation_needs_enough_ads(store: Store) -> None:
    result = analysis.run_valuation(store, NOW)
    assert result.median_error_pct is None
    assert result.note is not None


def test_compare_models_by_year_and_budget(market_store: Store) -> None:
    cars = analysis.load_snapshot(market_store, NOW)
    juke, golf = analysis.compare(
        cars, [("Nissan", "Juke"), ("Volkswagen", "Golf")], analysis.Filters(min_count=1), budget=8000
    )

    assert juke.n == 120 and golf.n == 121
    assert juke.by_year and all(n >= 3 for _, n in juke.by_year.values())
    assert juke.budget_n < juke.n
    assert juke.budget_price_med is not None and juke.budget_price_med <= 8000
    assert len(juke.scatter) == 120


def test_gearbox_kind() -> None:
    assert analysis.gearbox_kind("Manuelni 6 brzina") == "manuelni"
    assert analysis.gearbox_kind("Automatski / poluautomatski") == "automatski"
    assert analysis.gearbox_kind(None) is None
