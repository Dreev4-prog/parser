from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from app_version import APP_VERSION
from db import init_db
from vinted_lab import (
    VINTED_QUEUE, complete_category, make_worker_id, mark_category_running,
    save_catalog_page, scan_collects_detail_metrics,
)
from vinted_probe import VintedProbeClient, VintedProbeConfig

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vinted-scan-worker")


async def _heartbeat(worker_id: str, state: dict[str, Any]) -> None:
    while True:
        try:
            await VINTED_QUEUE.heartbeat(role="scan", worker_id=worker_id, payload=state)
        except Exception as exc:
            log.warning("Vinted Scan heartbeat failed: %s", exc)
        await asyncio.sleep(7)


async def _process_task(client: VintedProbeClient, fields: dict[str, str], state: dict[str, Any]) -> None:
    scan_id = int(fields.get("scan_id") or 0)
    catalog_id = int(fields.get("catalog_id") or 0)
    category_name = str(fields.get("category_name") or f"Catalog {catalog_id}")
    pages_target = max(1, min(15, int(fields.get("pages") or 3)))
    if scan_id <= 0 or catalog_id <= 0:
        return
    if not await mark_category_running(scan_id, catalog_id):
        return
    collect_detail_metrics = await scan_collects_detail_metrics(scan_id)

    state.update({"active": 1, "scan_id": scan_id, "catalog_id": catalog_id, "category": category_name, "page": 0, "pages_target": pages_target})
    seen: set[int] = set()
    duplicate_count = 0
    fetched_pages = 0
    target_unique = pages_target * client.config.per_page
    # Radar fairness is page-based: every terminal category gets the same primary
    # depth and never receives hidden recovery pages. Manual Parser keeps bounded
    # unique-depth recovery for its existing behaviour.
    radar_mode = not collect_detail_metrics
    max_pages = pages_target if radar_mode else pages_target + client.config.recovery_pages
    final_status = "completed"
    error_text = ""
    started = time.monotonic()
    try:
        for page in range(1, max_pages + 1):
            state["page"] = page
            items, outcome, _response_time = await client.fetch_catalog_page(catalog_id, page)
            fetched_pages = page
            if outcome != "ok":
                final_status = "partial" if seen else "failed"
                error_text = outcome
                break
            page_new: list[Any] = []
            page_dupes = 0
            for item in items:
                if item.item_id in seen:
                    page_dupes += 1
                    continue
                seen.add(item.item_id)
                page_new.append(item)
            duplicate_count += page_dupes
            new_ids = await save_catalog_page(
                scan_id=scan_id,
                catalog_id=catalog_id,
                category_name=category_name,
                page=page,
                items=page_new,
                duplicate_count=page_dupes,
            )
            if collect_detail_metrics:
                for item_id in new_ids:
                    await VINTED_QUEUE.enqueue_metric(scan_id=scan_id, item_id=item_id)
            state.update({"unique": len(seen), "duplicates": duplicate_count, "last_outcome": outcome})
            if len(items) < client.config.per_page:
                break
            if radar_mode and page >= pages_target:
                break
            if not radar_mode and page >= pages_target and len(seen) >= target_unique:
                break
        if not radar_mode and fetched_pages >= max_pages and len(seen) < target_unique:
            final_status = "partial"
            error_text = f"unique_depth {len(seen)}/{target_unique}"
    except Exception as exc:
        final_status = "partial" if seen else "failed"
        error_text = f"{type(exc).__name__}: {exc}"
        log.exception("Vinted category failed scan=%s catalog=%s", scan_id, catalog_id)
    finally:
        await complete_category(
            scan_id=scan_id,
            catalog_id=catalog_id,
            status=final_status,
            pages_fetched=fetched_pages,
            unique_items=len(seen),
            duplicate_count=duplicate_count,
            error_text=error_text,
        )
        state.update({
            "active": 0,
            "scan_id": 0,
            "catalog_id": 0,
            "category": "",
            "page": 0,
            "pages_target": 0,
            "processed_categories": int(state.get("processed_categories", 0)) + 1,
            "last_seconds": round(time.monotonic() - started, 2),
            "last_unique": len(seen),
            "last_status": final_status,
        })


async def main() -> None:
    if not os.getenv("REDIS_URL", "").strip():
        raise RuntimeError("Vinted Scan Worker requires REDIS_URL")
    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError("Vinted Scan Worker requires DATABASE_URL")
    await init_db()
    await VINTED_QUEUE.ensure_groups()
    worker_id = make_worker_id("vscan")
    state: dict[str, Any] = {"active": 0, "processed_categories": 0, "last_status": "starting"}
    hb = asyncio.create_task(_heartbeat(worker_id, state))
    config = VintedProbeConfig.from_env()
    # Scan worker is catalog-only. Keep page transport fast and never call item detail.
    config.detail_sample = 0
    config.page_concurrency = 1
    config.min_interval_seconds = max(0.10, min(1.0, float(os.getenv("VINTED_SCAN_MIN_INTERVAL_SECONDS", "0.20"))))
    log.info("DT Vinted Scan Worker online | version=%s worker=%s per_page=%s recovery=%s", APP_VERSION, worker_id, config.per_page, config.recovery_pages)
    try:
        async with VintedProbeClient(config) as client:
            bootstrap = await client.bootstrap()
            state["bootstrap"] = bootstrap
            while True:
                claimed = await VINTED_QUEUE.claim(role="scan", worker_id=worker_id)
                if not claimed:
                    continue
                msg_id, fields = claimed
                try:
                    await _process_task(client, fields, state)
                finally:
                    await VINTED_QUEUE.ack(role="scan", msg_id=msg_id)
    finally:
        hb.cancel()
        await VINTED_QUEUE.close()


if __name__ == "__main__":
    asyncio.run(main())
