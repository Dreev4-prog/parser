from __future__ import annotations

import asyncio
import logging
import os

# This process owns Radar background work only. It never starts Telegram polling
# or user scan workers, but it shares PostgreSQL/Redis with the main parser.
os.environ["RADAR_DEDICATED_WORKER"] = "1"
os.environ["STABLE_SINGLE_SERVICE_MODE"] = "0"
os.environ["FORCE_LOCAL_MODE"] = "0"
os.environ["RAILWAY_REQUIRES_REDIS"] = "1"
os.environ.setdefault("DISTRIBUTED_WORKERS", "1")
os.environ.setdefault("DB_POOL_SIZE", "3")
os.environ.setdefault("DB_MAX_OVERFLOW", "2")

from aiogram import Bot

from app_version import APP_VERSION
from bot import (
    BOT_TOKEN,
    RADAR_ANALYTICS_SNAPSHOT_REFRESH_TIMEOUT_SECONDS,
    RADAR_STATS_SNAPSHOT_INTERVAL_SECONDS,
    RADAR_STATS_SNAPSHOT_REFRESH_TIMEOUT_SECONDS,
    load_radar_autoscan_state,
    organic_velocity_scheduler,
    radar_autoscan_scheduler,
    radar_maintenance_scheduler,
    radar_v3_observation_scheduler,
    refresh_radar_analytics_snapshot,
)
from db import DATABASE_BACKEND, init_db
from distributed import COORDINATOR, REDIS_URL
from radar import refresh_radar_stats_snapshot


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-radar-worker")


async def _publish_stats_snapshot() -> bool:
    try:
        stats, updated_at = await asyncio.wait_for(
            refresh_radar_stats_snapshot(),
            timeout=RADAR_STATS_SNAPSHOT_REFRESH_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("Radar Worker statistics snapshot failed", exc_info=True)
        return False
    log.info(
        "Radar Worker statistics published at=%s total=%s hot=%s rising=%s "
        "fast_sold=%s categories=%s signals=%s",
        updated_at.isoformat(), stats.total, stats.hot, stats.rising,
        stats.fast_sold, stats.categories, stats.signals,
    )
    return True


async def _publish_deep_analytics_snapshot() -> bool:
    try:
        _text, updated_at = await asyncio.wait_for(
            refresh_radar_analytics_snapshot(),
            timeout=RADAR_ANALYTICS_SNAPSHOT_REFRESH_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("Radar Worker deep analytics snapshot failed", exc_info=True)
        return False
    log.info("Radar Worker deep analytics published at=%s", updated_at.isoformat())
    return True


async def _publish_all_snapshots() -> tuple[bool, bool]:
    # Run sequentially: both snapshots scan large Radar tables and should never
    # compete with each other for PostgreSQL I/O.
    public_ok = await _publish_stats_snapshot()
    analytics_ok = await _publish_deep_analytics_snapshot()
    return public_ok, analytics_ok


async def radar_stats_snapshot_scheduler() -> None:
    """Refresh exact public counters only while AutoScan is not crawling."""
    await asyncio.sleep(30)
    while True:
        try:
            state = await load_radar_autoscan_state()
            if str(state.get("status") or "idle") == "running":
                await asyncio.sleep(30)
                continue
            await _publish_all_snapshots()
            await asyncio.sleep(RADAR_STATS_SNAPSHOT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Radar Worker statistics scheduler failed")
            await asyncio.sleep(60)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required by Radar Worker for admin notifications")
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is required by Radar Worker")

    await init_db()
    await COORDINATOR.connect()
    await COORDINATOR.ensure_group()
    bot = Bot(BOT_TOKEN)

    # Publish the first durable snapshot before starting a resumed/new crawl.
    # The main Telegram service can display this single-row record immediately.
    for attempt in range(1, 4):
        public_ok, analytics_ok = await _publish_all_snapshots()
        if public_ok and analytics_ok:
            break
        log.warning(
            "Radar Worker initial snapshot retry=%s/3 public=%s analytics=%s",
            attempt, public_ok, analytics_ok,
        )
        if attempt < 3:
            await asyncio.sleep(10)

    tasks = [
        asyncio.create_task(radar_maintenance_scheduler(), name="radar-worker-maintenance"),
        asyncio.create_task(radar_v3_observation_scheduler(), name="radar-worker-observations"),
        asyncio.create_task(organic_velocity_scheduler(), name="radar-worker-organic-velocity"),
        asyncio.create_task(radar_autoscan_scheduler(bot), name="radar-worker-autoscan"),
        asyncio.create_task(radar_stats_snapshot_scheduler(), name="radar-worker-public-stats"),
    ]
    log.warning(
        "Dedicated DT Radar Worker online | version=%s db=%s snapshot_interval=%ss tasks=%s",
        APP_VERSION, DATABASE_BACKEND, RADAR_STATS_SNAPSHOT_INTERVAL_SECONDS, len(tasks),
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await COORDINATOR.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
