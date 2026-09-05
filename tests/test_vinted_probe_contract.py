from __future__ import annotations

import json

import httpx
import pytest

from vinted_probe import (
    VintedProbeConfig,
    evaluate_quality_gates,
    normalize_catalog_item,
    normalize_detail_metrics,
    normalize_public_item_html,
    run_probe,
)


def test_catalog_normalizer_preserves_identity_and_metrics():
    item = normalize_catalog_item({
        "id": 123,
        "title": "Nike Test",
        "price": {"amount": "44.50", "currency_code": "EUR"},
        "path": "/items/123-nike-test",
        "brand_title": "Nike",
        "size_title": "42",
        "catalog_id": 257,
        "view_count": 19,
        "favourite_count": 4,
        "promoted": True,
        "user": {"id": 55, "login": "seller"},
    })
    assert item is not None
    assert item.item_id == 123
    assert item.url.endswith("/items/123-nike-test")
    assert item.price_amount == 44.5
    assert item.catalog_view_count == 19
    assert item.catalog_favourite_count == 4
    assert item.promoted is True


def test_detail_wrong_identity_never_returns_views():
    sample = normalize_detail_metrics({"item": {"id": 999, "view_count": 123}}, expected_item_id=123)
    assert sample.identity_ok is False
    assert sample.outcome == "wrong_identity"
    assert sample.view_count is None


def test_missing_view_count_stays_unknown_not_zero():
    sample = normalize_detail_metrics({"item": {"id": 123, "favourite_count": 8}}, expected_item_id=123)
    assert sample.identity_ok is True
    assert sample.view_count is None
    assert sample.favourite_count == 8
    assert any("UNKNOWN" in note for note in sample.notes)


def _mock_item_html(item_id: int, views: int | None = None) -> str:
    views = item_id if views is None else views
    payload = {
        "item": {
            "id": item_id,
            "view_count": views,
            "favourite_count": 3,
            "created_at": "2026-09-05T07:00:00Z",
            "is_closed": False,
        }
    }
    encoded = json.dumps(json.dumps(payload, separators=(",", ":")))
    return f'<html><script>self.__next_f.push([1,{encoded}])</script></html>'


def test_public_html_parser_identity_and_metrics():
    sample = normalize_public_item_html(_mock_item_html(123, views=19), expected_item_id=123)
    assert sample.identity_ok is True
    assert sample.view_count == 19
    assert sample.favourite_count == 3
    assert sample.upload_raw == "2026-09-05T07:00:00Z"


def test_public_html_wrong_identity_fails_closed():
    sample = normalize_public_item_html(_mock_item_html(999, views=19), expected_item_id=123)
    assert sample.identity_ok is False
    assert sample.view_count is None


@pytest.mark.asyncio
async def test_probe_full_mock_pass():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="ok", headers={"content-type": "text/html"})
        if request.url.path == "/api/v2/catalog/items":
            page = int(request.url.params.get("page", "1"))
            assert request.url.params.get("time") is not None
            payload = {
                "pagination": {"time": 1700000000 + page},
                "items": [
                    {"id": page * 10 + 1, "title": "A", "url": f"https://www.vinted.de/items/{page * 10 + 1}-a", "view_count": 0, "favourite_count": 1},
                    {"id": page * 10 + 2, "title": "B", "url": f"https://www.vinted.de/items/{page * 10 + 2}-b", "view_count": 2, "favourite_count": 0},
                ]
            }
            return httpx.Response(200, json=payload)
        if request.url.path.startswith("/api/v2/items/") and request.url.path.endswith("/details"):
            item_id = int(request.url.path.split("/")[-2])
            return httpx.Response(200, json={"item": {
                "id": item_id,
                "view_count": item_id,
                "favourite_count": 3,
                "created_at_ts": "2026-09-05T07:00:00Z",
                "is_closed": False,
            }})
        if request.url.path.startswith("/items/"):
            item_id = int(request.url.path.split("/")[-1].split("-", 1)[0])
            return httpx.Response(200, text=_mock_item_html(item_id), headers={"content-type": "text/html"})
        return httpx.Response(404)

    config = VintedProbeConfig(
        base_url="https://www.vinted.de",
        pages=2,
        per_page=2,
        page_concurrency=2,
        detail_sample=4,
        detail_concurrency=2,
        min_interval_seconds=0,
    )
    report = await run_probe(config, transport=httpx.MockTransport(handler))
    assert report["catalog"]["unique_items"] == 4
    assert report["catalog"]["depth_recovery"][0]["recovery_complete"] is True
    assert report["detail"]["identity_ok"] == 4
    assert report["detail"]["exact_view_samples"] == 4
    assert report["quality_gates"]["radar_ready"]["pass"] is True


@pytest.mark.asyncio
async def test_probe_401_fails_closed():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="ok")
        return httpx.Response(401, json={"code": 100, "message_code": "invalid_authentication_token"})

    config = VintedProbeConfig(pages=1, detail_sample=0, min_interval_seconds=0)
    report = await run_probe(config, transport=httpx.MockTransport(handler))
    assert report["catalog"]["unique_items"] == 0
    assert report["catalog"]["failures"][0]["outcome"] == "authentication_required"
    assert report["quality_gates"]["catalog_access"]["pass"] is False


def test_quality_gate_requires_exact_views():
    report = {
        "catalog": {"unique_items": 10, "failures": []},
        "detail": {"requested": 4, "identity_ok": 4, "exact_view_samples": 0},
    }
    gates = evaluate_quality_gates(report)
    assert gates["catalog_access"]["pass"] is True
    assert gates["identity"]["pass"] is True
    assert gates["exact_views"]["pass"] is False
    assert gates["radar_ready"]["pass"] is False


@pytest.mark.asyncio
async def test_probe_recovers_unique_depth_from_live_page_overlap():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="ok")
        if request.url.path == "/api/v2/catalog/items":
            page = int(request.url.params.get("page", "1"))
            pages = {
                1: [101, 100],
                2: [100, 99],   # one overlap
                3: [99, 98],    # recovery page restores requested 4 unique
            }
            ids = pages.get(page, [])
            return httpx.Response(200, json={
                "pagination": {"time": 1700000000 + page},
                "items": [{"id": item_id, "title": str(item_id), "url": f"https://www.vinted.de/items/{item_id}-x"} for item_id in ids],
            })
        return httpx.Response(404)

    config = VintedProbeConfig(
        pages=2,
        per_page=2,
        recovery_pages=2,
        detail_sample=0,
        min_interval_seconds=0,
    )
    report = await run_probe(config, transport=httpx.MockTransport(handler))
    depth = report["catalog"]["depth_recovery"][0]
    assert depth["fetched_pages"] == 3
    assert depth["recovery_pages_used"] == 1
    assert depth["unique_seen"] == 4
    assert depth["recovery_complete"] is True
    assert report["quality_gates"]["pagination_integrity"]["pass"] is True
