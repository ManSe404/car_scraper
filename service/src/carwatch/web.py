"""UI routes for the analysis pages (market, compare, deals) and the collection runs page."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from carwatch import analysis, jobs
from carwatch.charts import km_scatter_svg, year_lines_svg
from carwatch.config import Settings
from carwatch.crawl import Crawler
from carwatch.store import Store

log = logging.getLogger(__name__)

MAX_COMPARE = 6
MAX_SCATTER = 3


def _int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value.replace(".", "").replace(" ", ""))
    except ValueError:
        return None


def filters_from(params: Any, min_count_default: int = 10) -> analysis.Filters:
    def text(name: str) -> str | None:
        value = params.get(name)
        return value or None

    return analysis.Filters(
        price_min=_int(params.get("price_min")),
        price_max=_int(params.get("price_max")),
        year_min=_int(params.get("year_min")),
        year_max=_int(params.get("year_max")),
        km_max=_int(params.get("km_max")),
        fuel=text("fuel"),
        gearbox=text("gearbox"),
        chassis=text("chassis"),
        brand=text("brand"),
        model=text("model"),
        include_damaged=bool(params.get("damaged")),
        min_count=_int(params.get("min_count")) or min_count_default,
    )


def filter_query(f: analysis.Filters) -> dict[str, str]:
    """The filters as query parameters, to carry them from one page to another."""
    values = {
        "price_min": f.price_min, "price_max": f.price_max, "year_min": f.year_min, "year_max": f.year_max,
        "km_max": f.km_max, "fuel": f.fuel, "gearbox": f.gearbox, "chassis": f.chassis,
        "damaged": "1" if f.include_damaged else None,
    }  # fmt: skip
    return {k: str(v) for k, v in values.items() if v is not None}


def register(
    app: FastAPI,
    *,
    settings: Settings,
    store: Store,
    crawler: Crawler,
    templates: Jinja2Templates,
) -> None:
    def revalue() -> None:
        jobs.revalue(settings)

    def same_origin(request: Request) -> None:
        # Form posts from other sites carry their own Origin; only this UI may press these buttons.
        origin = request.headers.get("origin")
        if origin is None or urlparse(origin).hostname not in settings.allowed_hosts:
            raise HTTPException(status_code=403, detail="Zahtev ne dolazi sa Car Watch stranice")

    def facets(cars: list[analysis.Car]) -> dict[str, list[str]]:
        return {
            "fuels": sorted({c.fuel for c in cars if c.fuel}),
            "chassis": sorted({c.chassis for c in cars if c.chassis}),
            "brands": sorted({c.brand for c in cars}),
        }

    # ---- analysis pages --------------------------------------------------------------------------------

    @app.get("/market", response_class=HTMLResponse, name="market_page")
    def market_page(request: Request) -> HTMLResponse:
        cars = analysis.load_snapshot(store)
        f = filters_from(request.query_params)
        rows = analysis.market(cars, f)
        return templates.TemplateResponse(
            request,
            "market.html",
            {
                "rows": rows,
                "f": f,
                "facets": facets(cars),
                "snapshot_size": len(cars),
                "carry": urlencode(filter_query(f)),
                "carry_items": filter_query(f),
                "valuation": analysis.valuation_meta(store),
                "page": "market",
            },
        )

    @app.get("/compare", response_class=HTMLResponse, name="compare_page")
    def compare_page(request: Request) -> HTMLResponse:
        cars = analysis.load_snapshot(store)
        f = filters_from(request.query_params, min_count_default=1)
        budget = _int(request.query_params.get("budget"))
        keys: list[tuple[str, str]] = []
        for raw in request.query_params.getlist("m")[:MAX_COMPARE]:
            brand, _, model = raw.partition("|")
            if brand and model and (brand, model) not in keys:
                keys.append((brand, model))
        comparisons = analysis.compare(cars, keys, f, budget)
        labelled = [(f"{c.brand} {c.model}", i + 1, c) for i, c in enumerate(comparisons)]
        lines = year_lines_svg([(label, slot, c.by_year) for label, slot, c in labelled])
        scatter = (
            km_scatter_svg([(label, slot, c.scatter) for label, slot, c in labelled])
            if 0 < len(labelled) <= MAX_SCATTER
            else ""
        )
        years = sorted({y for _, _, c in labelled for y in c.by_year})
        return templates.TemplateResponse(
            request,
            "compare.html",
            {
                "series": labelled,
                "lines": lines,
                "scatter": scatter,
                "years": years,
                "budget": budget,
                "f": f,
                "keys": [f"{b}|{m}" for b, m in keys],
                "facets": facets(cars),
                "max_compare": MAX_COMPARE,
                "max_scatter": MAX_SCATTER,
                "page": "compare",
            },
        )

    @app.get("/deals", response_class=HTMLResponse, name="deals_page")
    def deals_page(request: Request) -> HTMLResponse:
        cars = analysis.load_snapshot(store)
        f = filters_from(request.query_params)
        return templates.TemplateResponse(
            request,
            "deals.html",
            {
                "rows": analysis.deals(cars, f),
                "f": f,
                "facets": facets(cars),
                "valuation": analysis.valuation_meta(store),
                "suspicious": analysis.SUSPICIOUS_DEAL,
                "page": "deals",
            },
        )

    @app.get("/runs", response_class=HTMLResponse, name="runs_page")
    def runs_page(request: Request) -> HTMLResponse:
        now = datetime.now(UTC)
        return templates.TemplateResponse(
            request,
            "runs.html",
            {
                "status": crawler.status(now),
                "runs": crawler.runs(),
                "valuation": analysis.valuation_meta(store),
                "valuating": jobs.revalue_running(settings),
                "settings": settings,
                "page": "runs",
            },
        )

    @app.post("/runs/start")
    def ui_start(request: Request) -> RedirectResponse:
        same_origin(request)
        crawler.pause(None)
        crawler.start_full_run(datetime.now(UTC))
        return RedirectResponse("/runs", status_code=303)

    @app.post("/runs/{run_id}/cancel")
    def ui_cancel(request: Request, run_id: int) -> RedirectResponse:
        same_origin(request)
        crawler.cancel_run(run_id, datetime.now(UTC))
        return RedirectResponse("/runs", status_code=303)

    @app.post("/runs/pause")
    def ui_pause(request: Request) -> RedirectResponse:
        same_origin(request)
        crawler.pause(datetime.now(UTC) + timedelta(hours=24))
        return RedirectResponse("/runs", status_code=303)

    @app.post("/runs/resume")
    def ui_resume(request: Request) -> RedirectResponse:
        same_origin(request)
        crawler.pause(None)
        return RedirectResponse("/runs", status_code=303)

    @app.post("/analysis/run")
    def ui_revalue(request: Request, background: BackgroundTasks) -> RedirectResponse:
        same_origin(request)
        background.add_task(revalue)
        return RedirectResponse("/runs", status_code=303)
