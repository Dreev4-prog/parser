from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

from app_version import APP_VERSION
from vinted_probe import VintedProbeConfig, format_probe_summary, run_probe


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("vinted-probe")


async def _notify_admins(text: str) -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    ids = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    if not token or not ids:
        return
    async with httpx.AsyncClient(timeout=15.0) as client:
        for admin_id in ids:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": admin_id, "text": text[:3900], "disable_web_page_preview": True},
                )
            except httpx.HTTPError as exc:
                log.warning("Vinted Probe admin notification failed for %s: %s", admin_id, exc)


async def _run_once() -> dict:
    config = VintedProbeConfig.from_env()
    log.info(
        "DT Vinted Probe online | version=%s base=%s catalogs=%s pages=%s per_page=%s page_concurrency=%s detail_sample=%s detail_concurrency=%s token=%s",
        APP_VERSION,
        config.base_url,
        list(config.catalog_ids) or ["ALL"],
        config.pages,
        config.per_page,
        config.page_concurrency,
        config.detail_sample,
        config.detail_concurrency,
        "configured" if config.access_token_web else "anonymous/bootstrap",
    )
    report = await run_probe(config)
    summary = format_probe_summary(report)
    for line in summary.splitlines():
        log.info(line)
    log.info("VINTED_PROBE_REPORT_JSON=%s", json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if os.getenv("VINTED_PROBE_NOTIFY_ADMINS", "1").strip().lower() in {"1", "true", "yes", "on"}:
        await _notify_admins(summary)
    return report


async def main() -> None:
    repeat_minutes = max(0, int(os.getenv("VINTED_PROBE_REPEAT_MINUTES", "0")))
    while True:
        try:
            await _run_once()
        except Exception:
            log.exception("Vinted Probe failed unexpectedly")
        if repeat_minutes <= 0:
            # Railway should not restart a completed one-shot probe over and over.
            log.info("Vinted Probe completed. repeat=off; idling safely until service restart.")
            while True:
                await asyncio.sleep(3600)
        await asyncio.sleep(repeat_minutes * 60)


if __name__ == "__main__":
    asyncio.run(main())
