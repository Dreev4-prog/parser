from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from app_version import APP_VERSION
from db import init_db
from vinted_lab import (
    VINTED_QUEUE, load_metric_item, make_worker_id, mark_metric_error,
    mark_metric_processing, save_metric_sample,
)
from vinted_probe import VintedItem, VintedProbeClient, VintedProbeConfig

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vinted-metrics-worker")


async def _heartbeat(worker_id: str, state: dict[str, Any]) -> None:
    while True:
        try:
            await VINTED_QUEUE.heartbeat(role="metrics", worker_id=worker_id, payload=state)
        except Exception as exc:
            log.warning("Vinted Metrics heartbeat failed: %s", exc)
        await asyncio.sleep(7)


async def _process_task(client: VintedProbeClient, fields: dict[str, str], state: dict[str, Any]) -> None:
    scan_id = int(fields.get("scan_id") or 0)
    item_id = int(fields.get("item_id") or 0)
    if scan_id <= 0 or item_id <= 0:
        return
    row = await load_metric_item(scan_id, item_id)
    if row is None:
        return
    if not await mark_metric_processing(scan_id, item_id):
        return
    state.update({"active": 1, "scan_id": scan_id, "item_id": item_id})
    started = time.monotonic()
    try:
        item = VintedItem(
            item_id=item_id,
            title=row.title or "",
            url=row.url or f"{client.config.base_url}/items/{item_id}",
            price_amount=row.price_amount,
            currency=row.currency or "",
            brand=row.brand or "",
            size=row.size or "",
            condition=row.condition or "",
            seller_id=row.seller_id,
            seller_login=row.seller_login or "",
            catalog_id=row.catalog_id,
            promoted=row.promoted,
            visible=row.visible,
            catalog_view_count=row.catalog_view_count,
            catalog_favourite_count=row.catalog_favourite_count,
        )
        sample = await client.fetch_item_detail(item)
        await save_metric_sample(scan_id, item_id, sample)
        state["last_outcome"] = sample.outcome
        state["last_exact"] = bool(sample.identity_ok and sample.view_count is not None)
        if sample.view_count is not None:
            state["exact_total"] = int(state.get("exact_total", 0)) + 1
        else:
            state["unknown_total"] = int(state.get("unknown_total", 0)) + 1
    except Exception as exc:
        await mark_metric_error(scan_id, item_id, type(exc).__name__)
        state["errors"] = int(state.get("errors", 0)) + 1
        log.warning("Vinted metric failed scan=%s item=%s: %s", scan_id, item_id, exc)
    finally:
        try:
            await VINTED_QUEUE.clear_metric_marker(scan_id, item_id)
        except Exception:
            pass
        state.update({
            "active": 0,
            "scan_id": 0,
            "item_id": 0,
            "processed": int(state.get("processed", 0)) + 1,
            "last_ms": int((time.monotonic() - started) * 1000),
        })


async def main() -> None:
    if not os.getenv("REDIS_URL", "").strip():
        raise RuntimeError("Vinted Metrics Worker requires REDIS_URL")
    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError("Vinted Metrics Worker requires DATABASE_URL")
    await init_db()
    await VINTED_QUEUE.ensure_groups()
    worker_id = make_worker_id("vmetrics")
    state: dict[str, Any] = {"active": 0, "processed": 0, "exact_total": 0, "unknown_total": 0, "errors": 0}
    hb = asyncio.create_task(_heartbeat(worker_id, state))
    config = VintedProbeConfig.from_env()
    config.detail_concurrency = 1
    config.min_interval_seconds = max(0.15, min(2.0, float(os.getenv("VINTED_METRICS_MIN_INTERVAL_SECONDS", "0.35"))))
    log.info(
        "DT Vinted Metrics Worker online | version=%s worker=%s auth=%s",
        APP_VERSION, worker_id,
        "session" if (config.access_token_web or getattr(config, "session_json", "")) else "anonymous/bootstrap",
    )
    try:
        async with VintedProbeClient(config) as client:
            bootstrap = await client.bootstrap()
            state["bootstrap"] = bootstrap
            while True:
                claimed = await VINTED_QUEUE.claim(role="metrics", worker_id=worker_id)
                if not claimed:
                    continue
                msg_id, fields = claimed
                try:
                    await _process_task(client, fields, state)
                finally:
                    await VINTED_QUEUE.ack(role="metrics", msg_id=msg_id)
    finally:
        hb.cancel()
        await VINTED_QUEUE.close()


if __name__ == "__main__":
    asyncio.run(main())
