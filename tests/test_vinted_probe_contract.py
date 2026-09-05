from __future__ import annotations

import json

import httpx
import pytest

from vinted_probe import (
    VintedProbeConfig,
    evaluate_quality_gates,
    normalize_catalog_item,
    normalize_detail_metrics,
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


@pytest.mark.asyncio
async def test_probe_full_mock_pass():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="ok", headers={"content-type": "text/html"})
        if request.url.path == "/api/v2/catalog/items":
            page = int(request.url.params.get("page", "1"))
            payload = {
                "items": [
                    {"id": page * 10 + 1, "title": "A", "view_count": 0, "favourite_count": 1},
                    {"id": page * 10 + 2, "title": "B", "view_count": 2, "favourite_count": 0},
                ]
            }
            return httpx.Response(200, json=payload)
        if request.url.path.startswith("/api/v2/items/"):
            item_id = int(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"item": {"id": item_id, "view_count": item_id, "favourite_count": 3, "created_at": "2026-09-05T07:00:00Z"}})
        return httpx.Response(404)

    config = VintedProbeConfig(
        base_url="https://www.vinted.de",
        pages=2,
        per_page=96,
        page_concurrency=2,
        detail_sample=4,
        detail_concurrency=2,
        min_interval_seconds=0,
    )
    report = await run_probe(config, transport=httpx.MockTransport(handler))
    assert report["catalog"]["unique_items"] == 4
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
