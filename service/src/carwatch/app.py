"""FastAPI app: the small server-rendered UI over the database (`carwatch ui`). The extension does not use it."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from carwatch import __version__, fmt, web
from carwatch.charts import price_chart_svg
from carwatch.config import Settings
from carwatch.crawl import Crawler
from carwatch.store import SORTS, AdFilters, Store

log = logging.getLogger(__name__)

SORT_LABELS = {
    "last_seen": "Poslednji put viđen",
    "first_seen": "Prvi put viđen",
    "price": "Cena ↑",
    "price_desc": "Cena ↓",
    "drop": "Najveći pad cene",
    "mileage": "Kilometraža ↑",
    "year": "Godište ↓",
}

QUERY_LABELS = {
    "brand": "marka",
    "model": "model",
    "priceFrom": "cena od",
    "priceTo": "cena do",
    "yearFrom": "godište od",
    "yearTo": "godište do",
    "mileageTo": "km do",
    "fuel": "gorivo",
    "gearBox": "menjač",
    "chassis": "karoserija",
    "showOldNew": "stanje",
    "sort": "sortiranje",
}


def _int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value.replace(".", "").replace(" ", ""))
    except ValueError:
        return None


def search_label(query: dict[str, Any]) -> str:
    parts: list[str] = []
    model = query.get("model")
    models = ", ".join(map(str, model)) if isinstance(model, list) else str(model or "")
    title = " ".join(p for p in (str(query.get("brand") or ""), models) if p)
    if title:
        parts.append(title)
    for key, value in query.items():
        if key in {"brand", "model", "sort", "showOldNew"} or value in (None, "", []):
            continue
        label = QUERY_LABELS.get(key, key)
        shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        parts.append(f"{label} {shown}")
    return " · ".join(parts) or "sve"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.db_path)
    crawler = Crawler(store, settings)
    tz = settings.timezone

    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    templates.env.filters.update(
        money=fmt.money,
        signed_money=fmt.signed_money,
        km=fmt.km,
        thousands=fmt.thousands,
        date=partial(fmt.date, tz=tz),
        date_time=partial(fmt.date_time, tz=tz),
        days_since=fmt.days_since,
    )
    templates.env.globals.update(version=__version__)

    app = FastAPI(
        title="Car Watch",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.state.store = store

    # ---- UI -----------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, name="ads_page")
    def ads_page(
        request: Request,
        q: str | None = None,
        brand: str | None = None,
        model: str | None = None,
        search: str | None = None,
        price_min: str | None = None,
        price_max: str | None = None,
        year_min: str | None = None,
        km_max: str | None = None,
        dropped: str | None = None,
        active: str | None = None,
        sort: str = "last_seen",
    ) -> HTMLResponse:
        filters = AdFilters(
            q=q or None,
            brand=brand or None,
            model=model or None,
            search=search or None,
            price_min=_int(price_min),
            price_max=_int(price_max),
            year_min=_int(year_min),
            km_max=_int(km_max),
            dropped=bool(dropped),
            active_only=bool(active),
            sort=sort if sort in SORTS else "last_seen",
        )
        rows = store.list_ads(filters)
        search_name = None
        if filters.search:
            try:
                search_name = search_label(json.loads(filters.search))
            except ValueError:
                search_name = filters.search
        return templates.TemplateResponse(
            request,
            "ads.html",
            {
                "rows": rows,
                "f": filters,
                "facets": store.facets(),
                "stats": store.stats(),
                "sorts": SORT_LABELS,
                "search_name": search_name,
                "page": "ads",
            },
        )

    @app.get("/ads/{ad_id}", response_class=HTMLResponse, name="ad_page")
    def ad_page(request: Request, ad_id: int) -> HTMLResponse:
        ad, details, observations = store.get_ad(ad_id)
        if ad is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"ad_id": ad_id, "page": "ads"}, status_code=404
            )
        points = [(datetime.fromisoformat(o["seen_at"]), o["price"]) for o in observations if o["price"] is not None]
        # One sighting draws a lone dot; the chart starts to say something from the second one.
        chart = price_chart_svg(points, end=datetime.fromisoformat(ad["last_seen"]), tz=tz) if len(points) >= 2 else ""
        return templates.TemplateResponse(
            request,
            "ad.html",
            {
                "ad": ad,
                "details": details,
                "observations": observations,
                "chart": chart,
                "summary": store.summary(ad_id),
                "now": datetime.now(UTC),
                "page": "ads",
            },
        )

    @app.get("/searches", response_class=HTMLResponse, name="searches_page")
    def searches_page(request: Request) -> HTMLResponse:
        rows = []
        for row in store.list_searches():
            query = json.loads(row["query"])
            rows.append(
                {
                    "label": search_label(query),
                    "row": row,
                    "filter_url": "/?" + urlencode({"search": row["key"]}),
                }
            )
        return templates.TemplateResponse(request, "searches.html", {"rows": rows, "page": "searches"})

    web.register(app, settings=settings, store=store, crawler=crawler, templates=templates)
    return app
