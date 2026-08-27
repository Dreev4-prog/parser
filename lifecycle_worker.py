from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

# Dedicated Lifecycle Worker. It checks only strong, fresh Radar listings and
# never participates in category/page scanning. Keep the main bot's stable
# single-service traffic profile from leaking into this auxiliary service.
os.environ["STABLE_SINGLE_SERVICE_MODE"] = "0"
os.environ["FORCE_LOCAL_MODE"] = "1"
os.environ["RAILWAY_REQUIRES_REDIS"] = "0"
os.environ.setdefault("DISTRIBUTED_WORKERS", "1")
os.environ.setdefault("TRAFFIC_VIEW_CONCURRENCY", "4")
os.environ.setdefault("TRAFFIC_GLOBAL_CONCURRENCY", "4")
os.environ.setdefault("TRAFFIC_VIEW_MIN_INTERVAL_SECONDS", "0.20")

from app_version import APP_VERSION
from db import init_db
from parser import KleinanzeigenParser
from radar import (
    claim_due_lifecycle_watches,
    complete_lifecycle_check,
    lifecycle_queue_stats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-lifecycle-worker")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


LIFECYCLE_POLL_SECONDS = _env_int("LIFECYCLE_POLL_SECONDS", 10, 3, 60)
LIFECYCLE_CONCURRENCY = _env_int("LIFECYCLE_CONCURRENCY", 4, 1, 8)
LIFECYCLE_BATCH_SIZE = _env_int("LIFECYCLE_BATCH_SIZE", 20, 1, 80)
LIFECYCLE_LEASE_SECONDS = _env_int("LIFECYCLE_LEASE_SECONDS", 180, 60, 600)
LIFECYCLE_HEARTBEAT_SECONDS = _env_int("LIFECYCLE_HEARTBEAT_SECONDS", 60, 20, 300)


class LifecycleWorker:
    def __init__(self) -> None:
        self.worker_id = f"lifecycle-{socket.gethostname()}-{os.getpid()}"
        self.parser = KleinanzeigenParser()
        self.started_at = time.monotonic()
        self.checked = 0
        self.active = 0
        self.unknown = 0
        self.confirm_candidates = 0
        self.fast_sold = 0
        self.errors = 0
        self._sem = asyncio.Semaphore(LIFECYCLE_CONCURRENCY)
        self._last_heartbeat = 0.0

    async def close(self) -> None:
        await self.parser.close()

    async def _process_one(self, job) -> None:
        async with self._sem:
            result = None
            error_text = None
            try:
                result = await self.parser.check_listing_active(job.url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors += 1
                error_text = f"{type(exc).__name__}: {exc}"[:1000]
                log.warning(
                    "Lifecycle direct check failed watch=%s external_id=%s error=%s",
                    job.id, job.external_id, error_text,
                )
            try:
                new_status = await complete_lifecycle_check(
                    job.id, result, error_text=error_text,
                )
                self.checked += 1
                if result is True:
                    self.active += 1
                elif result is None:
                    self.unknown += 1
                elif new_status == "confirming":
                    self.confirm_candidates += 1
                elif new_status == "disappeared":
                    self.fast_sold += 1
                log.info(
                    "Lifecycle check watch=%s external_id=%s active=%s status=%s checks_before=%s",
                    job.id, job.external_id, result, new_status, job.checks,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.errors += 1
                log.exception(
                    "Lifecycle result persist failed watch=%s external_id=%s",
                    job.id, job.external_id,
                )

    async def _heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < LIFECYCLE_HEARTBEAT_SECONDS:
            return
        self._last_heartbeat = now
        try:
            stats = await lifecycle_queue_stats()
        except Exception:
            stats = {}
            log.debug("Lifecycle heartbeat stats unavailable", exc_info=True)
        log.info(
            "Lifecycle Worker heartbeat version=%s uptime=%ss checked=%s active=%s unknown=%s "
            "confirm_candidates=%s fast_sold=%s errors=%s queue=%s",
            APP_VERSION, int(now - self.started_at), self.checked, self.active, self.unknown,
            self.confirm_candidates, self.fast_sold, self.errors, stats,
        )

    async def run(self) -> None:
        log.warning(
            "DT Radar Lifecycle Worker online | version=%s concurrency=%s batch=%s poll=%ss",
            APP_VERSION, LIFECYCLE_CONCURRENCY, LIFECYCLE_BATCH_SIZE, LIFECYCLE_POLL_SECONDS,
        )
        while True:
            try:
                jobs = await claim_due_lifecycle_watches(
                    self.worker_id,
                    limit=LIFECYCLE_BATCH_SIZE,
                    lease_seconds=LIFECYCLE_LEASE_SECONDS,
                )
                if jobs:
                    await asyncio.gather(*(self._process_one(job) for job in jobs))
                    await asyncio.sleep(0)
                else:
                    await self._heartbeat()
                    await asyncio.sleep(LIFECYCLE_POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.errors += 1
                log.exception("Lifecycle Worker loop error")
                await asyncio.sleep(LIFECYCLE_POLL_SECONDS)


async def main() -> None:
    await init_db()
    worker = LifecycleWorker()
    try:
        await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
