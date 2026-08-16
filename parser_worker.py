from __future__ import annotations

import asyncio
import logging
import os

# v4.1.4: a dedicated parser executable is never a local-mode bot.
os.environ["DISTRIBUTED_WORKERS"] = "1"

from aiogram import Bot

from bot import (
    BOT_TOKEN,
    PARSER_WORKER_CONCURRENCY,
    STABLE_SCAN_ENGINE,
    distributed_scan_worker,
    distributed_worker_heartbeat,
    progress_ticker,
    recover_distributed_unfinished_scans,
)
from db import DATABASE_BACKEND, init_db
from parser import SCAN_TRANSPORT, SHARED_BROWSER_RUNTIME, shutdown_shared_browser_runtime
from distributed import COORDINATOR, DISTRIBUTED_WORKERS, default_worker_id
from stable_engine import cleanup_stable_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-parser-worker")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not DISTRIBUTED_WORKERS:
        raise RuntimeError("Parser worker requires REDIS_URL and DISTRIBUTED_WORKERS=1")

    await init_db()
    if STABLE_SCAN_ENGINE:
        try:
            cleaned = await cleanup_stable_state()
            if any(cleaned.values()):
                log.info("Stable state cleanup: %s", cleaned)
        except Exception:
            log.warning("Stable state cleanup failed", exc_info=True)
    await COORDINATOR.connect()
    await COORDINATOR.ensure_group()
    recovered = await recover_distributed_unfinished_scans()
    if recovered:
        log.warning("Recovered %s unfinished scan(s) into Redis queue", recovered)

    bot = Bot(BOT_TOKEN)
    base_worker_id = default_worker_id("parser")
    heartbeat = asyncio.create_task(
        distributed_worker_heartbeat(base_worker_id, "parser"), name="parser-heartbeat"
    )
    ticker = asyncio.create_task(progress_ticker(bot), name="parser-progress-ticker")
    workers = [
        asyncio.create_task(
            distributed_scan_worker(bot, f"{base_worker_id}-{idx}"),
            name=f"distributed-parser-{idx}",
        )
        for idx in range(1, PARSER_WORKER_CONCURRENCY + 1)
    ]
    log.info(
        "DT PARSER worker online | id=%s | replica=%s | local_concurrency=%s | transport=%s | db=%s",
        base_worker_id,
        os.getenv("RAILWAY_REPLICA_ID", "local"),
        PARSER_WORKER_CONCURRENCY,
        SCAN_TRANSPORT,
        DATABASE_BACKEND,
    )
    try:
        await asyncio.gather(*workers)
    finally:
        heartbeat.cancel()
        ticker.cancel()
        for task in workers:
            task.cancel()
        await asyncio.gather(heartbeat, ticker, *workers, return_exceptions=True)
        await COORDINATOR.close()
        if SHARED_BROWSER_RUNTIME:
            await shutdown_shared_browser_runtime()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
