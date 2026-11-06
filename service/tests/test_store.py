from __future__ import annotations

from datetime import UTC, datetime, timedelta

from carwatch.models import Capture
from carwatch.store import AdFilters, Store, search_key
from tests.conftest import load

T0 = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


def search_capture(at: datetime, price: int | None = None) -> Capture:
    data = load("capture_search.json")
    data["captured_at"] = at.isoformat()
    if price is not None:
        data["items"][0]["price"] = price
    return Capture.model_validate(data)


def ad_capture(at: datetime) -> Capture:
    data = load("capture_ad.json")
    data["captured_at"] = at.isoformat()
    return Capture.model_validate(data)


def count(store: Store, table: str) -> int:
    with store.connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_first_search_creates_ads_observations_and_search(store: Store) -> None:
    result = store.ingest(search_capture(T0))

    assert (result.ads_seen, result.new_ads, result.price_changes) == (2, 2, [])
    assert count(store, "ads") == 2
    assert count(store, "observations") == 2
    assert count(store, "searches") == 1
    ad, _, _ = store.get_ad(10000001)
    assert ad is not None
    assert (ad["price"], ad["first_price"], ad["min_price"], ad["max_price"]) == (6399, 6399, 6399, 6399)
    assert ad["is_dealer"] == 0
    assert ad["mileage_km"] == 223854


def test_reloading_the_same_page_does_not_add_sightings(store: Store) -> None:
    store.ingest(search_capture(T0))
    result = store.ingest(search_capture(T0 + timedelta(minutes=2)))

    assert result.new_ads == 0
    assert count(store, "observations") == 2
    assert count(store, "captures") == 2


def test_price_drop_is_detected_and_tracked(store: Store) -> None:
    store.ingest(search_capture(T0))
    result = store.ingest(search_capture(T0 + timedelta(days=1), price=5999))

    assert [(c.ad_id, c.old_price, c.new_price, c.delta) for c in result.price_changes] == [
        (10000001, 6399, 5999, -400)
    ]
    summary = store.summary(10000001)
    assert (summary.price, summary.first_price, summary.min_price, summary.max_price) == (
        5999,
        6399,
        5999,
        6399,
    )
    assert summary.price_changes == 1
    assert summary.days_seen == 2


def test_late_capture_does_not_overwrite_the_current_price(store: Store) -> None:
    store.ingest(search_capture(T0 + timedelta(days=2)))
    # A capture from earlier arrives late (it waited in the extension's outbox) with another price.
    result = store.ingest(search_capture(T0, price=6999))

    assert result.price_changes == []
    ad, _, observations = store.get_ad(10000001)
    assert ad is not None
    assert ad["price"] == 6399
    assert ad["first_price"] == 6999
    assert ad["max_price"] == 6999
    assert ad["first_seen"] == T0.isoformat()
    assert [o["price"] for o in observations] == [6999, 6399]


def test_ad_page_stores_details(store: Store) -> None:
    result = store.ingest(ad_capture(T0))

    assert result.new_ads == 1
    ad, details, _ = store.get_ad(10000003)
    assert ad is not None and details is not None
    assert ad["has_details"] == 1
    assert ad["published_at"] == "2026-10-07 12:42:40"
    assert ad["city"] == "Beograd"
    assert ad["is_dealer"] == 1
    assert details["damaged"] == "Nije oštećen"
    assert "[telefon]" in details["description"]


def test_search_after_ad_page_keeps_details_and_fills_the_image(store: Store) -> None:
    store.ingest(ad_capture(T0))
    data = load("capture_search.json")
    data["captured_at"] = (T0 + timedelta(hours=1)).isoformat()
    data["items"][0]["id"] = 10000003
    store.ingest(Capture.model_validate(data))

    ad, details, _ = store.get_ad(10000003)
    assert ad is not None and details is not None
    assert ad["image_url"] is not None
    assert ad["has_details"] == 1


def test_filters(store: Store) -> None:
    store.ingest(search_capture(T0))
    store.ingest(search_capture(T0 + timedelta(days=1), price=5999))
    key = search_key(load("capture_search.json")["search"]["query"])

    ids = lambda f: [r["id"] for r in store.list_ads(f)]  # noqa: E731
    assert ids(AdFilters(dropped=True)) == [10000001]
    assert ids(AdFilters(price_max=6000)) == [10000001]
    assert ids(AdFilters(q="Acenta")) == [10000002]
    assert sorted(ids(AdFilters(search=key))) == [10000001, 10000002]
    assert ids(AdFilters(sort="price")) == [10000001, 10000002]


def test_search_key_ignores_the_page_number() -> None:
    assert search_key({"brand": "Nissan", "page": 2}) == search_key({"page": 1, "brand": "Nissan"})


def test_unknown_ad_summary(store: Store) -> None:
    assert store.summary(42).known is False
