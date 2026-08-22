from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time

# Dedicated Page Worker: use the exact browser category-page parser already proven
# in the stable main service, but move page navigation to separate Railway power.
os.environ.setdefault("SCAN_TRANSPORT", "browser")
os.environ.setdefault("SHARED_BROWSER_RUNTIME", "1")
_ALLOW_LEGACY_TUNING = os.getenv("DT_ALLOW_LEGACY_WORKER_TUNING", "0").strip().lower() in {
    "1", "true", "yes", "on",
}

# v4.4.0 production fleet profile. Four Page Worker replicas share bounded
# Redis lanes instead of behaving like four unrelated two-context processes.
if not _ALLOW_LEGACY_TUNING:
    os.environ["PAGE_WORKER_CONCURRENCY"] = "2"
    os.environ["TRAFFIC_SCAN_CONCURRENCY"] = "2"
    os.environ["TRAFFIC_GLOBAL_CONCURRENCY"] = "3"
    os.environ["TRAFFIC_SCAN_MIN_INTERVAL_SECONDS"] = "0.35"
else:
    os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "2")
    os.environ.setdefault("TRAFFIC_GLOBAL_CONCURRENCY", "3")
    os.environ.setdefault("TRAFFIC_SCAN_MIN_INTERVAL_SECONDS", "0.35")
os.environ["STABLE_SINGLE_SERVICE_MODE"] = "0"
os.environ["DIST_TRAFFIC_SCAN_BUCKET"] = "page"
os.environ["DIST_TRAFFIC_BROWSER_BUCKET"] = "page-browser"
os.environ["DIST_TRAFFIC_GLOBAL_BUCKET"] = "search-fleet-perf480"
os.environ["DIST_TRAFFIC_COOLDOWN_BUCKET"] = "page-fleet-perf480"
os.environ["DIST_TRAFFIC_SCAN_LIMIT"] = "6"
os.environ["DIST_TRAFFIC_BROWSER_LIMIT"] = "4"
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = "8"
os.environ["DIST_TRAFFIC_SHARED_COOLDOWN"] = "0"

from app_version import APP_VERSION
from parser import KleinanzeigenParser, TemporaryAccessError, shutdown_shared_browser_runtime
from page_manager import (
    PAGE_CACHE_TTL_SECONDS,
    PAGE_ERROR_TTL_SECONDS,
    PAGE_GROUP,
    PAGE_HEARTBEAT_KEY,
    PAGE_PENDING_TTL_SECONDS,
    PAGE_REDIS_PREFIX,
    PAGE_RUNTIME_PREFIX,
    PAGE_STREAM,
    REDIS_URL,
    serialize_page_info,
)
from traffic import TRAFFIC

try:
    from redis.asyncio import Redis  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"redis package is required: {exc}") from exc

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-page-worker")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


PAGE_WORKER_CONCURRENCY = _env_int("PAGE_WORKER_CONCURRENCY", 2, 1, 4)
PAGE_WORKER_HEARTBEAT_SECONDS = _env_int("PAGE_WORKER_HEARTBEAT_SECONDS", 3, 1, 15)
PAGE_WORKER_RECLAIM_IDLE_MS = _env_int("PAGE_WORKER_RECLAIM_IDLE_MS", 120_000, 30_000, 300_000)
PAGE_WORKER_JOB_TIMEOUT_SECONDS = _env_int("PAGE_WORKER_JOB_TIMEOUT_SECONDS", 90, 20, 240)
PAGE_WORKER_STATUS_TTL_SECONDS = max(10, PAGE_WORKER_HEARTBEAT_SECONDS * 5)


class PageWorkerProcess:
    def __init__(self) -> None:
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not set")
        self.redis = Redis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=20,
        )
        self.base_id = f"page-{socket.gethostname()}-{os.getpid()}"
        self.started = time.monotonic()
        self.active = 0
        self.processed = 0
        self.cache_served = 0
        self.errors = 0
        self.http_403 = 0
        self.http_429 = 0
        self.rate_ema = 0.0
        self._group_ready = False
        self._stop = asyncio.Event()

        # Keep local category pressure bounded; Redis fleet guards cap the aggregate
        # across all four Page Worker replicas.
        TRAFFIC.base_scan_limit = max(1, PAGE_WORKER_CONCURRENCY)
        TRAFFIC.base_global_limit = max(2, PAGE_WORKER_CONCURRENCY + 1)

        original_report_refusal = TRAFFIC.report_refusal

        async def tracked_refusal(status_code: int, kind: str) -> None:
            code = int(status_code)
            if code == 403:
                self.http_403 += 1
            elif code == 429:
                self.http_429 += 1
            await original_report_refusal(code, kind)

        TRAFFIC.report_refusal = tracked_refusal  # type: ignore[method-assign]

    def cache_key(self, cache_id: str) -> str:
        return f"{PAGE_REDIS_PREFIX}:cache:{cache_id}"

    def pending_key(self, cache_id: str) -> str:
        return f"{PAGE_RUNTIME_PREFIX}:pending:{cache_id}"

    def error_key(self, cache_id: str) -> str:
        return f"{PAGE_RUNTIME_PREFIX}:error:{cache_id}"

    def worker_key(self) -> str:
        return f"{PAGE_RUNTIME_PREFIX}:worker:{self.base_id}"

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.redis.xgroup_create(PAGE_STREAM, PAGE_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc).upper():
                raise
        self._group_ready = True

    async def heartbeat(self) -> None:
        try:
            queue_depth = int(await self.redis.xlen(PAGE_STREAM))
        except Exception:
            queue_depth = -1
        try:
            traffic = await TRAFFIC.snapshot()
            penalty = int(traffic.penalty_level)
            cooldown = float(traffic.cooldown_seconds)
            refusals_60s = int(traffic.refusals_60s)
        except Exception:
            penalty = 0
            cooldown = 0.0
            refusals_60s = 0
        payload = {
            "ts": time.time(),
            "consumer": self.base_id,
            "version": APP_VERSION,
            "replica": os.getenv("RAILWAY_REPLICA_ID", "local"),
            "concurrency": PAGE_WORKER_CONCURRENCY,
            "fleet_scan_limit": int(os.environ.get("DIST_TRAFFIC_SCAN_LIMIT", "6")),
            "fleet_browser_limit": int(os.environ.get("DIST_TRAFFIC_BROWSER_LIMIT", "4")),
            "fleet_global_limit": int(os.environ.get("DIST_TRAFFIC_GLOBAL_LIMIT", "8")),
            "fleet_bucket": os.environ.get("DIST_TRAFFIC_GLOBAL_BUCKET", "search-fleet"),
            "active": self.active,
            "queue_depth": queue_depth,
            "processed": self.processed,
            "cache_served": self.cache_served,
            "errors": self.errors,
            "rate_ema": round(self.rate_ema, 3),
            "http_403": self.http_403,
            "http_429": self.http_429,
            "penalty": penalty,
            "cooldown_seconds": round(cooldown, 2),
            "traffic_wait_ms_avg": round(1000.0 * (float(getattr(traffic, "local_wait_seconds_total", 0.0) or 0.0) / max(1, int(getattr(traffic, "lease_acquires", 0) or 0))), 1) if 'traffic' in locals() else 0.0,
            "redis_wait_ms_avg": round(1000.0 * (float(getattr(traffic, "distributed_wait_seconds_total", 0.0) or 0.0) / max(1, int(getattr(traffic, "lease_acquires", 0) or 0))), 1) if 'traffic' in locals() else 0.0,
            "refusals_60s": refusals_60s,
            "cache_ttl": PAGE_CACHE_TTL_SECONDS,
            "uptime_seconds": int(time.monotonic() - self.started),
        }
        raw = json.dumps(payload, ensure_ascii=False)
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(PAGE_HEARTBEAT_KEY, raw, ex=PAGE_WORKER_STATUS_TTL_SECONDS)
        pipe.set(self.worker_key(), raw, ex=PAGE_WORKER_STATUS_TTL_SECONDS)
        await pipe.execute()

    async def heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Page Worker heartbeat failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=PAGE_WORKER_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _consume(self, consumer: str) -> tuple[str, dict] | None:
        await self.ensure_group()

        # v4.8.0: never let abandoned history outrank the page a user is waiting for.
        rows = await self.redis.xreadgroup(
            PAGE_GROUP, consumer, {PAGE_STREAM: ">"}, count=1, block=1
        )
        if rows and rows[0][1]:
            msg_id, fields = rows[0][1][0]
            return str(msg_id), dict(fields or {})

        try:
            claimed = await self.redis.xautoclaim(
                PAGE_STREAM,
                PAGE_GROUP,
                consumer,
                min_idle_time=PAGE_WORKER_RECLAIM_IDLE_MS,
                start_id="0-0",
                count=1,
            )
            messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
            if messages:
                msg_id, fields = messages[0]
                return str(msg_id), dict(fields or {})
        except Exception:
            log.debug("Page Worker XAUTOCLAIM failed", exc_info=True)

        rows = await self.redis.xreadgroup(
            PAGE_GROUP, consumer, {PAGE_STREAM: ">"}, count=1, block=3000
        )
        if not rows or not rows[0][1]:
            return None
        msg_id, fields = rows[0][1][0]
        return str(msg_id), dict(fields or {})

    async def _ack(self, message_id: str) -> None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.xack(PAGE_STREAM, PAGE_GROUP, message_id)
        pipe.xdel(PAGE_STREAM, message_id)
        await pipe.execute()

    async def consumer_loop(self, index: int) -> None:
        consumer = f"{self.base_id}-{index}"
        parser = KleinanzeigenParser()
        try:
            while not self._stop.is_set():
                try:
                    message = await self._consume(consumer)
                    if message is None:
                        continue
                    msg_id, fields = message
                    cache_id = str(fields.get("cache_id") or "")
                    url = str(fields.get("url") or "")
                    try:
                        requested_page = max(1, int(fields.get("page") or 1))
                    except Exception:
                        requested_page = 1
                    if not cache_id or not url:
                        await self._ack(msg_id)
                        continue

                    cached = await self.redis.get(self.cache_key(cache_id))
                    if cached:
                        self.cache_served += 1
                        await self.redis.delete(self.pending_key(cache_id), self.error_key(cache_id))
                        await self._ack(msg_id)
                        continue

                    self.active += 1
                    item_started = time.monotonic()
                    try:
                        info = await asyncio.wait_for(
                            parser.parse_category_page_info(url, requested_page),
                            timeout=PAGE_WORKER_JOB_TIMEOUT_SECONDS,
                        )
                        # v4.3.23: Redis is acceleration, never truth. A worker may
                        # hit a cold-browser challenge or a normalized/wrong page on
                        # its first navigation. Do not poison the shared cache with
                        # such a response; the foreground stable parser will retry it.
                        structurally_safe = (
                            bool(getattr(info, "request_matches_page", True))
                            and bool(getattr(info, "page_verified", False))
                            and not bool(getattr(info, "suspicious", False))
                        )
                        if not structurally_safe:
                            self.errors += 1
                            pipe = self.redis.pipeline(transaction=False)
                            pipe.set(
                                self.error_key(cache_id),
                                "weak-page-not-cached",
                                ex=PAGE_ERROR_TTL_SECONDS,
                            )
                            pipe.delete(self.pending_key(cache_id), self.cache_key(cache_id))
                            await pipe.execute()
                            log.warning(
                                "Page Worker rejected weak page page=%s verified=%s matches=%s suspicious=%s",
                                requested_page,
                                bool(getattr(info, "page_verified", False)),
                                bool(getattr(info, "request_matches_page", True)),
                                bool(getattr(info, "suspicious", False)),
                            )
                            continue

                        raw = serialize_page_info(info)
                        pipe = self.redis.pipeline(transaction=False)
                        pipe.set(self.cache_key(cache_id), raw, ex=PAGE_CACHE_TTL_SECONDS)
                        pipe.delete(self.pending_key(cache_id), self.error_key(cache_id))
                        await pipe.execute()
                        self.processed += 1
                        elapsed = max(0.001, time.monotonic() - item_started)
                        instant_rate = 1.0 / elapsed
                        self.rate_ema = instant_rate if self.rate_ema <= 0 else (0.82 * self.rate_ema + 0.18 * instant_rate)
                    except TemporaryAccessError as exc:
                        self.errors += 1
                        await self.redis.set(
                            self.error_key(cache_id),
                            f"temporary-access:{exc.status_code}",
                            ex=PAGE_ERROR_TTL_SECONDS,
                        )
                        await self.redis.delete(self.pending_key(cache_id))
                        log.warning("Page Worker temporary access page=%s http=%s", requested_page, exc.status_code)
                    except Exception as exc:
                        self.errors += 1
                        await self.redis.set(
                            self.error_key(cache_id),
                            f"{type(exc).__name__}:{str(exc)[:220]}",
                            ex=PAGE_ERROR_TTL_SECONDS,
                        )
                        await self.redis.delete(self.pending_key(cache_id))
                        log.warning("Page Worker page failed page=%s error=%s", requested_page, exc)
                    finally:
                        self.active = max(0, self.active - 1)
                        await self._ack(msg_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Page Worker consumer loop failed consumer=%s", consumer)
                    await asyncio.sleep(1.0)
        finally:
            await parser.close()

    async def run(self) -> None:
        await self.redis.ping()
        await self.ensure_group()
        hb = asyncio.create_task(self.heartbeat_loop(), name="page-worker-heartbeat")
        consumers = [
            asyncio.create_task(self.consumer_loop(i), name=f"page-worker-consumer-{i}")
            for i in range(1, PAGE_WORKER_CONCURRENCY + 1)
        ]
        await self.heartbeat()
        log.info(
            "DT PARSER Page Worker online | id=%s | replica=%s | concurrency=%s | cache=%ss",
            self.base_id,
            os.getenv("RAILWAY_REPLICA_ID", "local"),
            PAGE_WORKER_CONCURRENCY,
            PAGE_CACHE_TTL_SECONDS,
        )
        try:
            await asyncio.gather(*consumers)
        finally:
            self._stop.set()
            hb.cancel()
            for task in consumers:
                task.cancel()
            await asyncio.gather(hb, *consumers, return_exceptions=True)
            try:
                await self.redis.delete(self.worker_key())
            except Exception:
                pass
            try:
                await self.redis.aclose()
            except Exception:
                pass
            try:
                await shutdown_shared_browser_runtime()
            except Exception:
                pass


async def main() -> None:
    worker = PageWorkerProcess()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
