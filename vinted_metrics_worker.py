from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from app_version import APP_VERSION
from db import init_db
from vinted_browser_metrics import VintedBrowserMetricClient
from vinted_lab import (
    VINTED_QUEUE,
    claim_radar_followup_processing,
    load_metric_item,
    load_radar_followup_item,
    make_worker_id,
    mark_metric_error,
    mark_metric_processing,
    mark_radar_followup_error,
    save_metric_sample,
    save_radar_followup_sample,
)
from vinted_probe import DEFAULT_BASE_URL, VintedItem
from vinted_session_store import load_vinted_session_json, session_fingerprint

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vinted-metrics-worker")


async def _heartbeat(worker_id: str, state: dict[str, Any], provider: VintedBrowserMetricClient, *, session_from_env: bool) -> None:
    last_db_check = 0.0
    last_db_fingerprint = str(state.get("session_fingerprint") or "")
    while True:
        try:
            now = time.monotonic()
            if not session_from_env and now - last_db_check >= 10.0:
                last_db_check = now
                db_fingerprint = await session_fingerprint()
                if db_fingerprint and db_fingerprint != last_db_fingerprint:
                    raw = await load_vinted_session_json()
                    if raw:
                        status = await provider.reload_session(raw)
                        last_db_fingerprint = db_fingerprint
                        state["session_fingerprint"] = db_fingerprint
                        state["session_source"] = "admin-db"
                        state["session_configured"] = provider.configured
                        log.info("Vinted Metrics session hot-reloaded from admin DB | status=%s", status)
            state.update(provider.health_snapshot())
            await VINTED_QUEUE.heartbeat(role="metrics", worker_id=worker_id, payload=state)
        except Exception as exc:
            log.warning("Vinted Metrics heartbeat failed: %s", exc)
        await asyncio.sleep(5)


async def _process_radar_followup(provider: VintedBrowserMetricClient, fields: dict[str, str], state: dict[str, Any], slot: int) -> None:
    watch_id = int(fields.get("watch_id") or 0)
    step = int(fields.get("step") or 0)
    if watch_id <= 0:
        return
    if not await claim_radar_followup_processing(watch_id, step):
        return
    loaded = await load_radar_followup_item(watch_id)
    if loaded is None:
        await mark_radar_followup_error(watch_id, step, "source_item_missing")
        return
    _watch, row = loaded

    active_slots = dict(state.get("active_slots") or {})
    active_slots[str(slot)] = f"followup:{row.item_id}"
    state["active_slots"] = active_slots
    state["active"] = len(active_slots)
    started = time.monotonic()
    try:
        item = VintedItem(
            item_id=int(row.item_id),
            title=row.title or "",
            url=row.url or f"{provider.base_url}/items/{row.item_id}",
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
        sample = await provider.fetch_item_detail(item)
        result = await save_radar_followup_sample(watch_id, step, sample)
        state["followup_processed"] = int(state.get("followup_processed", 0)) + 1
        if result == "exact":
            state["followup_exact"] = int(state.get("followup_exact", 0)) + 1
        else:
            state["followup_unknown"] = int(state.get("followup_unknown", 0)) + 1
        state["last_followup_outcome"] = str(sample.outcome or result)[:80]
    except Exception as exc:
        await mark_radar_followup_error(watch_id, step, type(exc).__name__)
        state["followup_errors"] = int(state.get("followup_errors", 0)) + 1
        log.warning("Vinted Radar follow-up failed watch=%s step=%s slot=%s: %s", watch_id, step, slot, exc)
    finally:
        try:
            await VINTED_QUEUE.clear_radar_followup_marker(watch_id, step)
        except Exception:
            pass
        active_slots = dict(state.get("active_slots") or {})
        active_slots.pop(str(slot), None)
        state["active_slots"] = active_slots
        state["active"] = len(active_slots)
        state["last_ms"] = int((time.monotonic() - started) * 1000)
        state.update(provider.health_snapshot())


async def _process_task(provider: VintedBrowserMetricClient, fields: dict[str, str], state: dict[str, Any], slot: int) -> None:
    if str(fields.get("purpose") or "") == "radar_followup":
        await _process_radar_followup(provider, fields, state, slot)
        return
    scan_id = int(fields.get("scan_id") or 0)
    item_id = int(fields.get("item_id") or 0)
    if scan_id <= 0 or item_id <= 0:
        return
    row = await load_metric_item(scan_id, item_id)
    if row is None:
        return
    if not await mark_metric_processing(scan_id, item_id):
        return

    active_slots = dict(state.get("active_slots") or {})
    active_slots[str(slot)] = item_id
    state["active_slots"] = active_slots
    state["active"] = len(active_slots)
    state["scan_id"] = scan_id
    started = time.monotonic()
    try:
        item = VintedItem(
            item_id=item_id,
            title=row.title or "",
            url=row.url or f"{provider.base_url}/items/{item_id}",
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
        sample = await provider.fetch_item_detail(item)
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
        log.warning("Vinted metric failed scan=%s item=%s slot=%s: %s", scan_id, item_id, slot, exc)
    finally:
        try:
            await VINTED_QUEUE.clear_metric_marker(scan_id, item_id)
        except Exception:
            pass
        active_slots = dict(state.get("active_slots") or {})
        active_slots.pop(str(slot), None)
        state["active_slots"] = active_slots
        state["active"] = len(active_slots)
        state["processed"] = int(state.get("processed", 0)) + 1
        state["last_ms"] = int((time.monotonic() - started) * 1000)
        state.update(provider.health_snapshot())


async def _slot_loop(worker_id: str, provider: VintedBrowserMetricClient, state: dict[str, Any], slot: int) -> None:
    while True:
        claimed = await VINTED_QUEUE.claim(role="metrics", worker_id=f"{worker_id}-s{slot}")
        if not claimed:
            continue
        msg_id, fields = claimed
        try:
            await _process_task(provider, fields, state, slot)
        finally:
            await VINTED_QUEUE.ack(role="metrics", msg_id=msg_id)


async def main() -> None:
    if not os.getenv("REDIS_URL", "").strip():
        raise RuntimeError("Vinted Metrics Worker requires REDIS_URL")
    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError("Vinted Metrics Worker requires DATABASE_URL")
    await init_db()
    await VINTED_QUEUE.ensure_groups()

    worker_id = make_worker_id("vmetrics")
    pool_size = max(1, min(8, int(os.getenv("VINTED_METRICS_CONCURRENCY", "4") or 4)))
    min_interval = max(0.08, min(2.0, float(os.getenv("VINTED_METRICS_MIN_INTERVAL_SECONDS", "0.18") or 0.18)))
    env_session = os.getenv("VINTED_SESSION_JSON", "").strip()
    db_session = "" if env_session else await load_vinted_session_json()
    raw_session = env_session or db_session
    provider = VintedBrowserMetricClient(
        base_url=(os.getenv("VINTED_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        session_json=raw_session,
        concurrency=pool_size,
        min_interval_seconds=min_interval,
    )
    provider_status = await provider.start()
    db_fingerprint = "" if env_session else await session_fingerprint()

    state: dict[str, Any] = {
        "active": 0,
        "active_slots": {},
        "processed": 0,
        "exact_total": 0,
        "unknown_total": 0,
        "errors": 0,
        "followup_processed": 0,
        "followup_exact": 0,
        "followup_unknown": 0,
        "followup_errors": 0,
        "concurrency": pool_size,
        "session_configured": provider.configured,
        "session_source": "env" if env_session else ("admin-db" if db_session else "missing"),
        "session_fingerprint": db_fingerprint if not env_session else provider.session_signature(),
    }
    state.update(provider.health_snapshot())
    log.info(
        "DT Vinted Metrics Worker online | version=%s worker=%s provider=%s session=%s concurrency=%s interval=%.2fs",
        APP_VERSION,
        worker_id,
        provider_status,
        ("env" if env_session else "admin-db") if provider.configured else "missing",
        pool_size,
        min_interval,
    )
    if not provider.configured:
        log.warning("Vinted session missing: open Admin -> Vinted Lab -> Vinted Session; worker will hot-reload after save")

    hb = asyncio.create_task(_heartbeat(worker_id, state, provider, session_from_env=bool(env_session)))
    slots = [asyncio.create_task(_slot_loop(worker_id, provider, state, idx + 1)) for idx in range(pool_size)]
    try:
        await asyncio.gather(*slots)
    finally:
        for task in slots:
            task.cancel()
        hb.cancel()
        await provider.close()
        await VINTED_QUEUE.close()


if __name__ == "__main__":
    asyncio.run(main())
