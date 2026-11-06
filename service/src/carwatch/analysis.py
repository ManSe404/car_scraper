"""Market analysis over the latest snapshot of the site.

* market:  per brand/model statistics (median price, year, km, power, yearly loss of value)
* compare: median price by year and what a budget buys, for a few chosen models
* valuation: a fair-price model (gradient boosting on log price) with out-of-fold predictions, so every
  ad's "expected price" comes from a model that never saw that ad; deal = how far below that it is listed
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OrdinalEncoder, TargetEncoder
from threadpoolctl import threadpool_limits

from carwatch.store import Store, _iso

log = logging.getLogger(__name__)

MIN_PRICE, MAX_PRICE = 300, 500_000
MIN_TRAINING_ADS = 150
SNAPSHOT_FALLBACK = timedelta(days=14)
GOOD_DEAL = 0.10
SUSPICIOUS_DEAL = 0.40
# With every core, OpenMP threads in gradient boosting fight each other and a small fit can take minutes.
MODEL_THREADS = min(4, os.cpu_count() or 1)


def gearbox_kind(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("automat") or "poluautomat" in lowered:
        return "automatski"
    if "manuel" in lowered:
        return "manuelni"
    return None


@dataclass
class Car:
    id: int
    title: str
    url: str
    brand: str
    model: str
    year: int | None
    km: int | None
    fuel: str | None
    gearbox: str | None
    chassis: str | None
    power_kw: int | None
    engine_cc: int | None
    price: int
    is_dealer: bool | None
    city: str | None
    image_url: str | None
    published_at: str | None
    damaged: bool | None
    from_abroad: bool | None
    has_details: bool
    expected: int | None
    deal_pct: float | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.brand, self.model)


@dataclass
class Filters:
    price_min: int | None = None
    price_max: int | None = None
    year_min: int | None = None
    year_max: int | None = None
    km_max: int | None = None
    fuel: str | None = None
    gearbox: str | None = None
    chassis: str | None = None
    brand: str | None = None
    model: str | None = None
    include_damaged: bool = False
    min_count: int = 10

    def keep(self, car: Car) -> bool:
        checks = [
            self.price_min is None or car.price >= self.price_min,
            self.price_max is None or car.price <= self.price_max,
            self.year_min is None or (car.year is not None and car.year >= self.year_min),
            self.year_max is None or (car.year is not None and car.year <= self.year_max),
            self.km_max is None or (car.km is not None and car.km <= self.km_max),
            self.fuel is None or car.fuel == self.fuel,
            self.gearbox is None or car.gearbox == self.gearbox,
            self.chassis is None or car.chassis == self.chassis,
            self.brand is None or car.brand == self.brand,
            self.model is None or car.model == self.model,
            self.include_damaged or not car.damaged,
        ]
        return all(checks)


# ---- snapshot ----------------------------------------------------------------------------------------


def snapshot_since(conn: sqlite3.Connection, now: datetime) -> str:
    """Ads seen since the start of the last finished full pass; or the last two weeks before the first one."""
    row = conn.execute("SELECT MAX(started_at) FROM runs WHERE kind = 'full' AND status = 'done'").fetchone()
    if row and row[0]:
        return str(row[0])
    return _iso(now - SNAPSHOT_FALLBACK)


def load_snapshot(store: Store, now: datetime | None = None) -> list[Car]:
    now = now or datetime.now(UTC)
    with store.connect() as conn:
        since = snapshot_since(conn, now)
        rows = conn.execute(
            """SELECT a.*, d.data AS details, v.expected_price, v.deal_pct
               FROM ads a
               LEFT JOIN ad_details d ON d.ad_id = a.id
               LEFT JOIN valuations v ON v.ad_id = a.id
               WHERE a.last_seen >= ? AND a.price BETWEEN ? AND ?
                 AND a.brand IS NOT NULL AND a.model IS NOT NULL
                 AND COALESCE(a.status, 'active') = 'active'""",
            (since, MIN_PRICE, MAX_PRICE),
        ).fetchall()
    cars = []
    for r in rows:
        details: dict[str, Any] = json.loads(r["details"]) if r["details"] else {}
        damaged_text = details.get("damaged")
        cars.append(
            Car(
                id=r["id"],
                title=r["title"],
                url=r["url"],
                brand=r["brand"],
                model=r["model"],
                year=r["year"],
                km=r["mileage_km"],
                fuel=r["fuel"],
                gearbox=gearbox_kind(r["gearbox"]),
                chassis=r["chassis"],
                power_kw=r["power_kw"],
                engine_cc=r["engine_cc"],
                price=r["price"],
                is_dealer=None if r["is_dealer"] is None else bool(r["is_dealer"]),
                city=r["city"],
                image_url=r["image_url"],
                published_at=r["published_at"],
                damaged=None if damaged_text is None else damaged_text != "Nije oštećen",
                from_abroad=details.get("from_abroad"),
                has_details=bool(r["has_details"]),
                expected=r["expected_price"],
                deal_pct=r["deal_pct"],
            )
        )
    return cars


# ---- statistics ---------------------------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def _pct(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def yearly_loss(cars: list[Car]) -> float | None:
    """Share of value lost per extra year of age, from a log-linear fit of price on year."""
    pairs = [(c.year, c.price) for c in cars if c.year is not None]
    years = {y for y, _ in pairs}
    if len(pairs) < 15 or len(years) < 3:
        return None
    x = np.array([y for y, _ in pairs], dtype=float)
    y = np.log(np.array([p for _, p in pairs], dtype=float))
    slope = float(np.polyfit(x, y, 1)[0])
    return 1.0 - math.exp(-slope)


@dataclass
class ModelStats:
    brand: str
    model: str
    n: int
    price_p25: float | None
    price_med: float | None
    price_p75: float | None
    year_med: float | None
    km_med: float | None
    kw_med: float | None
    loss_per_year: float | None
    good_deals: int


def market(cars: list[Car], f: Filters) -> list[ModelStats]:
    groups: dict[tuple[str, str], list[Car]] = defaultdict(list)
    for car in cars:
        if f.keep(car):
            groups[car.key].append(car)
    stats = []
    for (brand, model), group in groups.items():
        if len(group) < f.min_count:
            continue
        prices = [float(c.price) for c in group]
        stats.append(
            ModelStats(
                brand=brand,
                model=model,
                n=len(group),
                price_p25=_pct(prices, 25),
                price_med=_median(prices),
                price_p75=_pct(prices, 75),
                year_med=_median([float(c.year) for c in group if c.year is not None]),
                km_med=_median([float(c.km) for c in group if c.km is not None]),
                kw_med=_median([float(c.power_kw) for c in group if c.power_kw]),
                loss_per_year=yearly_loss(group),
                good_deals=sum(1 for c in group if c.deal_pct is not None and c.deal_pct >= GOOD_DEAL),
            )
        )
    return sorted(stats, key=lambda s: s.n, reverse=True)


@dataclass
class ModelComparison:
    brand: str
    model: str
    n: int
    by_year: dict[int, tuple[float, int]] = field(default_factory=dict)
    budget_n: int = 0
    budget_year_med: float | None = None
    budget_km_med: float | None = None
    budget_price_med: float | None = None
    scatter: list[tuple[int, int, int]] = field(default_factory=list)  # (km, price, ad id)


def compare(cars: list[Car], keys: list[tuple[str, str]], f: Filters, budget: int | None) -> list[ModelComparison]:
    result = []
    rng = random.Random(0)
    for brand, model in keys:
        group = [c for c in cars if c.key == (brand, model) and f.keep(c)]
        comparison = ModelComparison(brand=brand, model=model, n=len(group))
        per_year: dict[int, list[float]] = defaultdict(list)
        for c in group:
            if c.year is not None:
                per_year[c.year].append(float(c.price))
        comparison.by_year = {
            year: (float(np.median(prices)), len(prices))
            for year, prices in sorted(per_year.items())
            if len(prices) >= 3
        }
        in_budget = [c for c in group if budget is None or c.price <= budget]
        comparison.budget_n = len(in_budget)
        comparison.budget_year_med = _median([float(c.year) for c in in_budget if c.year is not None])
        comparison.budget_km_med = _median([float(c.km) for c in in_budget if c.km is not None])
        comparison.budget_price_med = _median([float(c.price) for c in in_budget])
        points = [(c.km, c.price, c.id) for c in group if c.km is not None]
        comparison.scatter = rng.sample(points, min(len(points), 300))
        result.append(comparison)
    return result


def deals(cars: list[Car], f: Filters, limit: int = 100) -> list[tuple[Car, int]]:
    """Ads furthest below their expected price, with the number of comparable ads of the same model."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for car in cars:
        counts[car.key] += 1
    ranked = [
        (c, counts[c.key])
        for c in cars
        if c.deal_pct is not None and c.price >= 500 and f.keep(c) and counts[c.key] >= f.min_count
    ]
    ranked.sort(key=lambda pair: pair[0].deal_pct or 0.0, reverse=True)
    return ranked[:limit]


# ---- fair price model ---------------------------------------------------------------------------------

# Columns 0-4: model key, brand, fuel, gearbox, chassis. Then the numeric ones below. Damage and import are
# only known for ads whose page was fetched; a column with too few known values is left out of the model.
_NUMERIC: list[str] = ["year", "km", "power_kw", "engine_cc", "is_dealer", "damaged", "from_abroad"]
_MIN_KNOWN = 30


def _as_float(values: np.ndarray) -> np.ndarray:
    return values.astype(float)


def _numeric_value(car: Car, name: str) -> float:
    value = getattr(car, name)
    return float("nan") if value is None else float(value)


def usable_numeric(cars: list[Car]) -> list[str]:
    return [n for n in _NUMERIC if sum(1 for c in cars if not math.isnan(_numeric_value(c, n))) >= _MIN_KNOWN]


def _matrix(cars: list[Car], numeric: list[str]) -> np.ndarray:
    rows = [
        [f"{c.brand}|{c.model}", c.brand, c.fuel or "?", c.gearbox or "?", c.chassis or "?",
         *(_numeric_value(c, n) for n in numeric)]
        for c in cars
    ]  # fmt: skip
    return np.array(rows, dtype=object)


def _pipeline(numeric_count: int) -> Pipeline:
    encoder_folds = KFold(n_splits=5, shuffle=True, random_state=0)
    columns = ColumnTransformer(
        [
            ("model", TargetEncoder(target_type="continuous", cv=encoder_folds), [0]),
            (
                "categories",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=np.nan,
                    encoded_missing_value=np.nan,
                    dtype=float,
                ),
                [1, 2, 3, 4],
            ),
            ("numbers", FunctionTransformer(_as_float), list(range(5, 5 + numeric_count))),
        ]
    )
    model = HistGradientBoostingRegressor(
        categorical_features=[1, 2, 3, 4],
        max_iter=400,
        learning_rate=0.06,
        l2_regularization=1.0,
        random_state=0,
    )
    return Pipeline([("columns", columns), ("model", model)])


@dataclass
class ValuationResult:
    ads: int
    median_error_pct: float | None
    computed_at: datetime
    note: str | None = None


def run_valuation(store: Store, now: datetime | None = None) -> ValuationResult:
    """Fit the fair-price model on the current snapshot and store expected prices for every ad in it."""
    now = now or datetime.now(UTC)
    cars = [c for c in load_snapshot(store, now) if c.year is not None and c.year >= 1985 and (c.km or 0) < 1_500_000]
    if len(cars) < MIN_TRAINING_ADS:
        result = ValuationResult(ads=len(cars), median_error_pct=None, computed_at=now,
                                 note=f"premalo oglasa za model ({len(cars)} < {MIN_TRAINING_ADS})")  # fmt: skip
        _save_meta(store, result)
        return result

    numeric = usable_numeric(cars)
    X = _matrix(cars, numeric)
    y = np.log(np.array([c.price for c in cars], dtype=float))
    folds = KFold(n_splits=5, shuffle=True, random_state=0)
    with threadpool_limits(limits=MODEL_THREADS):
        predicted = np.exp(cross_val_predict(_pipeline(len(numeric)), X, y, cv=folds))
    prices = np.exp(y)
    error = float(np.median(np.abs(predicted - prices) / prices))

    with store.connect() as conn:
        conn.execute("DELETE FROM valuations")
        conn.executemany(
            "INSERT INTO valuations (ad_id, expected_price, deal_pct, computed_at) VALUES (?, ?, ?, ?)",
            [
                (c.id, round(float(p)), float((p - c.price) / p), _iso(now))
                for c, p in zip(cars, predicted, strict=True)
            ],
        )
    result = ValuationResult(ads=len(cars), median_error_pct=error, computed_at=now)
    _save_meta(store, result)
    log.info("valuation: %d ads, median error %.1f%%", len(cars), error * 100)
    return result


def _save_meta(store: Store, result: ValuationResult) -> None:
    value = json.dumps(
        {
            "ads": result.ads,
            "median_error_pct": result.median_error_pct,
            "computed_at": _iso(result.computed_at),
            "note": result.note,
        }
    )
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO crawl_state (key, value) VALUES ('valuation', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (value,),
        )


def valuation_meta(store: Store) -> dict[str, Any] | None:
    with store.connect() as conn:
        row = conn.execute("SELECT value FROM crawl_state WHERE key = 'valuation'").fetchone()
    return json.loads(row["value"]) if row and row["value"] else None
