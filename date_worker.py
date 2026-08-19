from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime

# Date Worker uses the exact same category page parser as the stable bot. It only
# returns chronology hints; the main bot locally revalidates the final boundary.
os.environ.setdefault("SCAN_TRANSPORT", "browser")
os.environ.setdefault("SHARED_BROWSER_RUNTIME", "1")
os.environ.setdefault("STABLE_SINGLE_SERVICE_MODE", "1")
os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "2")
os.environ.setdefault("TRAFFIC_GLOBAL_CONCURRENCY", "3")
os.environ.setdefault("TRAFFIC_SCAN_MIN_INTERVAL_SECONDS", "0.30")

from parser import KleinanzeigenParser, TemporaryAccessError, profile_page_dates, shutdown_shared_browser_runtime
from date_manager import (
    DATE_CACHE_TTL_SECONDS,
    DATE_ERROR_TTL_SECONDS,
    DATE_GROUP,
    DATE_HEARTBEAT_KEY,
    DATE_PENDING_TTL_SECONDS,
    DATE_REDIS_PREFIX,
    DATE_STREAM,
    REDIS_URL,
)
from traffic import TRAFFIC

try:
    from redis.asyncio import Redis  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"redis package is required: {exc}") from exc

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-date-worker")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


DATE_WORKER_CONCURRENCY = _env_int("DATE_WORKER_CONCURRENCY", 2, 1, 4)
DATE_WORKER_HEARTBEAT_SECONDS = _env_int("DATE_WORKER_HEARTBEAT_SECONDS", 3, 1, 15)
DATE_WORKER_RECLAIM_IDLE_MS = _env_int("DATE_WORKER_RECLAIM_IDLE_MS", 90_000, 30_000, 300_000)
DATE_WORKER_JOB_TIMEOUT_SECONDS = _env_int("DATE_WORKER_JOB_TIMEOUT_SECONDS", 45, 15, 120)
DATE_WORKER_STATUS_TTL_SECONDS = max(10, DATE_WORKER_HEARTBEAT_SECONDS * 5)


class DateWorkerProcess:
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
        self.base_id = f"date-{socket.gethostname()}-{os.getpid()}"
        self.started = time.monotonic()
        self.active = 0
        self.processed = 0
        self.errors = 0
        self.http_403 = 0
        self.http_429 = 0
        self.rate_ema = 0.0
        self._group_ready = False
        self._stop = asyncio.Event()

        TRAFFIC.base_scan_limit = max(1, DATE_WORKER_CONCURRENCY)
        TRAFFIC.base_global_limit = max(2, DATE_WORKER_CONCURRENCY + 1)
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
        return f"{DATE_REDIS_PREFIX}:cache:{cache_id}"

    def pending_key(self, cache_id: str) -> str:
        return f"{DATE_REDIS_PREFIX}:pending:{cache_id}"

    def error_key(self, cache_id: str) -> str:
        return f"{DATE_REDIS_PREFIX}:error:{cache_id}"

    def worker_key(self) -> str:
        return f"{DATE_REDIS_PREFIX}:worker:{self.base_id}"

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.redis.xgroup_create(DATE_STREAM, DATE_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc).upper():
                raise
        self._group_ready = True

    async def heartbeat(self) -> None:
        try:
            queue_depth = int(await self.redis.xlen(DATE_STREAM))
        except Exception:
            queue_depth = -1
        try:
            traffic = await TRAFFIC.snapshot()
            penalty = int(traffic.penalty_level)
            cooldown = float(traffic.cooldown_seconds)
        except Exception:
            penalty = 0
            cooldown = 0.0
        payload = {
            "ts": time.time(),
            "consumer": self.base_id,
            "version": "4.3.24",
            "replica": os.getenv("RAILWAY_REPLICA_ID", "local"),
            "concurrency": DATE_WORKER_CONCURRENCY,
            "active": self.active,
            "queue_depth": queue_depth,
            "processed": self.processed,
            "errors": self.errors,
            "rate_ema": round(self.rate_ema, 3),
            "http_403": self.http_403,
            "http_429": self.http_429,
            "penalty": penalty,
            "cooldown_seconds": round(cooldown, 2),
            "cache_ttl": DATE_CACHE_TTL_SECONDS,
            "uptime_seconds": int(time.monotonic() - self.started),
        }
        raw = json.dumps(payload, ensure_ascii=False)
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(DATE_HEARTBEAT_KEY, raw, ex=DATE_WORKER_STATUS_TTL_SECONDS)
        pipe.set(self.worker_key(), raw, ex=DATE_WORKER_STATUS_TTL_SECONDS)
        await pipe.execute()

    async def heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Date Worker heartbeat failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=DATE_WORKER_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _consume(self, consumer: str) -> tuple[str, dict] | None:
        await self.ensure_group()
        try:
            claimed = await self.redis.xautoclaim(
                DATE_STREAM,
                DATE_GROUP,
                consumer,
                min_idle_time=DATE_WORKER_RECLAIM_IDLE_MS,
                start_id="0-0",
                count=1,
            )
            messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
            if messages:
                msg_id, fields = messages[0]
                return str(msg_id), dict(fields or {})
        except Exception:
            log.debug("Date Worker XAUTOCLAIM failed", exc_info=True)

        rows = await self.redis.xreadgroup(
            DATE_GROUP,
            consumer,
            {DATE_STREAM: ">"},
            count=1,
            block=3000,
        )
        if not rows:
            return None
        _stream, messages = rows[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        return str(msg_id), dict(fields or {})

    async def _ack(self, message_id: str) -> None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.xack(DATE_STREAM, DATE_GROUP, message_id)
        pipe.xdel(DATE_STREAM, message_id)
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
                    target_date = str(fields.get("target_date") or "")
                    try:
                        requested_page = max(1, min(50, int(fields.get("page") or 1)))
                    except Exception:
                        requested_page = 1
                    if not cache_id or not url or not target_date:
                        await self._ack(msg_id)
                        continue

                    if await self.redis.get(self.cache_key(cache_id)):
                        await self.redis.delete(self.pending_key(cache_id), self.error_key(cache_id))
                        await self._ack(msg_id)
                        continue

                    self.active += 1
                    started = time.monotonic()
                    try:
                        target_day = datetime.strptime(target_date, "%Y-%m-%d").date()
                        info = await asyncio.wait_for(
                            parser.parse_category_page_info(url, requested_page),
                            timeout=DATE_WORKER_JOB_TIMEOUT_SECONDS,
                        )
                        profile = profile_page_dates(info.items, target_day)
                        dated_count = max(0, len(info.items) - int(getattr(info, "missing_date_count", 0) or 0))
                        structurally_safe = (
                            bool(getattr(info, "request_matches_page", True))
                            and bool(getattr(info, "page_verified", False))
                            and not bool(getattr(info, "suspicious", False))
                            and profile.relation in {"target", "newer", "older", "mixed", "empty"}
                            and (
                                not info.items
                                or dated_count >= 2
                                or profile.relation == "target"
                            )
                        )
                        if not structurally_safe:
                            self.errors += 1
                            pipe = self.redis.pipeline(transaction=False)
                            pipe.set(self.error_key(cache_id), "weak-date-probe", ex=DATE_ERROR_TTL_SECONDS)
                            pipe.delete(self.pending_key(cache_id), self.cache_key(cache_id))
                            await pipe.execute()
                            log.warning(
                                "Date Worker rejected weak probe page=%s relation=%s verified=%s matches=%s suspicious=%s dated=%s",
                                requested_page,
                                profile.relation,
                                bool(getattr(info, "page_verified", False)),
                                bool(getattr(info, "request_matches_page", True)),
                                bool(getattr(info, "suspicious", False)),
                                dated_count,
                            )
                            continue

                        days = sorted(profile.days)
                        payload = {
                            "page": requested_page,
                            "relation": profile.relation,
                            "max_page": getattr(info, "max_page", None),
                            "actual_page": getattr(info, "actual_page", None),
                            "date_coverage": float(getattr(profile, "coverage", 0.0) or 0.0),
                            "target_count": int(getattr(profile, "target_count", 0) or 0),
                            "newer_count": int(getattr(profile, "newer_count", 0) or 0),
                            "older_count": int(getattr(profile, "older_count", 0) or 0),
                            "newest_day": days[-1].isoformat() if days else "",
                            "oldest_day": days[0].isoformat() if days else "",
                            "source": "date-worker",
                        }
                        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        pipe = self.redis.pipeline(transaction=False)
                        pipe.set(self.cache_key(cache_id), raw, ex=DATE_CACHE_TTL_SECONDS)
                        pipe.delete(self.pending_key(cache_id), self.error_key(cache_id))
                        await pipe.execute()
                        self.processed += 1
                        elapsed = max(0.001, time.monotonic() - started)
                        instant = 1.0 / elapsed
                        self.rate_ema = instant if self.rate_ema <= 0 else (0.82 * self.rate_ema + 0.18 * instant)
                    except TemporaryAccessError as exc:
                        self.errors += 1
                        await self.redis.set(
                            self.error_key(cache_id),
                            f"temporary-access:{exc.status_code}",
                            ex=DATE_ERROR_TTL_SECONDS,
                        )
                        await self.redis.delete(self.pending_key(cache_id))
                    except Exception as exc:
                        self.errors += 1
                        await self.redis.set(
                            self.error_key(cache_id),
                            f"{type(exc).__name__}:{str(exc)[:220]}",
                            ex=DATE_ERROR_TTL_SECONDS,
                        )
                        await self.redis.delete(self.pending_key(cache_id))
                        log.warning("Date Worker probe failed page=%s error=%s", requested_page, exc)
                    finally:
                        self.active = max(0, self.active - 1)
                        await self._ack(msg_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Date Worker consumer loop failed consumer=%s", consumer)
                    await asyncio.sleep(1.0)
        finally:
            await parser.close()

    async def run(self) -> None:
        await self.redis.ping()
        await self.ensure_group()
        hb = asyncio.create_task(self.heartbeat_loop(), name="date-worker-heartbeat")
        consumers = [
            asyncio.create_task(self.consumer_loop(i), name=f"date-worker-consumer-{i}")
            for i in range(1, DATE_WORKER_CONCURRENCY + 1)
        ]
        await self.heartbeat()
        log.info(
            "DT PARSER Date Worker online | id=%s | replica=%s | concurrency=%s | cache=%ss",
            self.base_id,
            os.getenv("RAILWAY_REPLICA_ID", "local"),
            DATE_WORKER_CONCURRENCY,
            DATE_CACHE_TTL_SECONDS,
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
    worker = DateWorkerProcess()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
