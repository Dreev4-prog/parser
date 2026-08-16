from __future__ import annotations

import asyncio
import logging
import os

# v4.1.2: this executable is always a distributed worker.
os.environ["DISTRIBUTED_WORKERS"] = "1"

from aiogram import Bot

from bot import (
    BOT_TOKEN,
    OBSERVATION_CONCURRENCY,
    backfill_recent_observation_plans,
    cleanup_obsolete_observation_plans,
    distributed_worker_heartbeat,
    observation_scheduler,
    recover_running_observations,
)
from db import DATABASE_BACKEND, init_db
from distributed import COORDINATOR, DISTRIBUTED_WORKERS, default_worker_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-views-worker")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not DISTRIBUTED_WORKERS:
        raise RuntimeError("Views worker requires REDIS_URL and DISTRIBUTED_WORKERS=1")

    await init_db()
    await COORDINATOR.connect()
    removed = await cleanup_obsolete_observation_plans()
    recovered = await recover_running_observations()
    planned = await backfill_recent_observation_plans()

    bot = Bot(BOT_TOKEN)
    worker_id = default_worker_id("views")
    heartbeat = asyncio.create_task(
        distributed_worker_heartbeat(worker_id, "views"), name="views-heartbeat"
    )
    workers = [
        asyncio.create_task(observation_scheduler(bot, idx), name=f"views-observation-{idx}")
        for idx in range(1, OBSERVATION_CONCURRENCY + 1)
    ]
    log.info(
        "DT PARSER views worker online | id=%s | concurrency=%s | db=%s | cleanup=%s recovered=%s planned=%s",
        worker_id,
        OBSERVATION_CONCURRENCY,
        DATABASE_BACKEND,
        removed,
        recovered,
        planned,
    )
    try:
        await asyncio.gather(*workers)
    finally:
        heartbeat.cancel()
        for task in workers:
            task.cancel()
        await asyncio.gather(heartbeat, *workers, return_exceptions=True)
        await COORDINATOR.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
