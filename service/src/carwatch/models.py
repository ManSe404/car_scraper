"""The capture contract between the extension and the service.

The extension turns the site's own JSON into these shapes, so a change on the site only touches
``extension/lib/extract.ts``. Seller data never reaches the service: the extension keeps an allow-list
of car fields and drops everything else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SearchItem(_Model):
    """One ad as it appears in a search results page."""

    id: int
    title: str
    url: str
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    fuel: str | None = None
    engine_cc: int | None = None
    power_kw: int | None = None
    horse_power: int | None = None
    gearbox: str | None = None
    chassis: str | None = None
    doors: str | None = None
    seats: str | None = None
    price: int | None = None
    base_price: int | None = None
    currency: str | None = None
    discount_pct: float | None = None
    status: str | None = None
    condition_new: bool | None = None
    registration_in_price: bool | None = None
    city: str | None = None
    is_dealer: bool | None = None
    image_url: str | None = None
    images_count: int | None = None
    tags: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    lifted_at: str | None = None
    featured: bool = False


class AdDetail(SearchItem):
    """One ad as it appears on its own page: everything from the search row plus the details."""

    mark: str | None = None
    color: str | None = None
    interior_color: str | None = None
    interior_material: str | None = None
    drive: str | None = None
    emission_class: str | None = None
    air_condition: str | None = None
    damaged: str | None = None
    registration: str | None = None
    origin: str | None = None
    country_origin: str | None = None
    from_abroad: bool | None = None
    ownership: bool | None = None
    reserved: bool | None = None
    price_fixed: bool | None = None
    price_offer: bool | None = None
    trade: str | None = None
    wheel_side: str | None = None
    equipment: list[str] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    condition: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    description: str | None = None
    published_at: str | None = None
    renewed_at: str | None = None
    registration_cost_min: int | None = None
    registration_cost_max: int | None = None


class SearchInfo(_Model):
    query: dict[str, object] = Field(default_factory=dict)
    page: int | None = None
    page_count: int | None = None
    total_items: int | None = None


class Capture(_Model):
    kind: Literal["search", "ad"]
    page_url: str
    captured_at: datetime
    extension_version: str | None = None
    # Set when the capture answers a task the service handed to the extension's worker tab.
    task_id: int | None = None
    search: SearchInfo | None = None
    items: list[SearchItem] = Field(default_factory=list)
    ad: AdDetail | None = None


class PriceChange(_Model):
    ad_id: int
    title: str
    old_price: int
    new_price: int

    @property
    def delta(self) -> int:
        return self.new_price - self.old_price


class AdSummary(_Model):
    """What the badge on an ad page shows."""

    known: bool
    id: int
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    days_seen: int = 0
    first_price: int | None = None
    price: int | None = None
    min_price: int | None = None
    max_price: int | None = None
    price_changes: int = 0
    published_at: str | None = None
    ui_url: str | None = None


class IngestResult(_Model):
    ok: bool = True
    capture_id: int
    ads_seen: int
    new_ads: int
    price_changes: list[PriceChange] = Field(default_factory=list)
    summary: AdSummary | None = None


# ---- collection work -----------------------------------------------------------------------------


class WorkTask(_Model):
    """One request for the extension's worker tab: a search results page or an ad page."""

    id: int
    kind: Literal["search", "detail"]
    path: str
    params: dict[str, str] = Field(default_factory=dict)


class WorkBatch(_Model):
    tasks: list[WorkTask] = Field(default_factory=list)
    gap_seconds: float
    paused_until: datetime | None = None


class TaskFailure(_Model):
    """What the worker reports when a request did not give usable data.

    blocked: the site refused (403/429/503 or a challenge page); all work pauses.
    gone:    the ad no longer exists.
    retry:   transient (the site deployed a new build); hand the task out again.
    drift:   the JSON changed shape; error: anything else.
    """

    reason: Literal["blocked", "gone", "retry", "drift", "error"]
    status: int | None = None
    message: str | None = None


class RunProgress(_Model):
    id: int
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    tasks_total: int
    tasks_done: int
    tasks_failed: int
    ads_seen: int
    note: str | None = None
    # Started by the service on its own (the weekly full pass), not by the user.
    auto: bool = False


class WorkStatus(_Model):
    has_work: bool
    # The automatic collection switch: on, the service starts full passes and plans detail pages by itself.
    auto: bool
    paused_until: datetime | None = None
    run: RunProgress | None = None
    next_full_run: datetime | None = None
    details_today: int
    details_limit: int
