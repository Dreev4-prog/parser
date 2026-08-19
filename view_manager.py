from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

log = logging.getLogger("dtparser-view-manager")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


REDIS_URL = os.getenv("REDIS_URL", "").strip()
REMOTE_VIEW_WORKER_ENABLED = _env_bool("REMOTE_VIEW_WORKER_ENABLED", False)
VIEW_REDIS_PREFIX = os.getenv("VIEW_REDIS_PREFIX", "dtparser:viewcounter").strip() or "dtparser:viewcounter"
VIEW_STREAM = f"{VIEW_REDIS_PREFIX}:jobs"
VIEW_GROUP = f"{VIEW_REDIS_PREFIX}:workers"
VIEW_HEARTBEAT_KEY = f"{VIEW_REDIS_PREFIX}:heartbeat"
VIEW_JOB_TTL_SECONDS = _env_int("VIEW_JOB_TTL_SECONDS", 3600, 300, 24 * 3600)
VIEW_RESULT_TTL_SECONDS = _env_int("VIEW_RESULT_TTL_SECONDS", 900, 60, 6 * 3600)
VIEW_REMOTE_TIMEOUT_SECONDS = _env_int("VIEW_REMOTE_TIMEOUT_SECONDS", 1800, 60, 7200)
VIEW_HEARTBEAT_STALE_SECONDS = _env_int("VIEW_HEARTBEAT_STALE_SECONDS", 20, 8, 120)
VIEW_PROGRESS_POLL_MS = _env_int("VIEW_PROGRESS_POLL_MS", 500, 100, 3000)


@dataclass
class RemoteViewResult:
    views: int | None
    source: str
    raw_text: str | None = None
    final_url: str | None = None
    page_title: str | None = None
    error: str | None = None


class RemoteViewManager:
    """Small Redis client used only for exact public view-count batches.

    This is deliberately independent from DISTRIBUTED_WORKERS. The Telegram bot
    and category parser may stay in v4.3.8 stable single-service mode while only
    the expensive view phase is delegated to a dedicated Railway service.
    """

    def __init__(self) -> None:
        self.url = REDIS_URL
        self.enabled = bool(REMOTE_VIEW_WORKER_ENABLED and self.url)
        self._redis: Any | None = None
        self._lock = asyncio.Lock()

    async def connect(self):
        if not self.enabled:
            return None
        if Redis is None:
            raise RuntimeError("redis package is not installed")
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=4,
                    socket_timeout=8,
                    health_check_interval=20,
                )
                await self._redis.ping()
        return self._redis

    async def close(self) -> None:
        redis = self._redis
        self._redis = None
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass

    def _payload_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:payload"

    def _progress_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:progress"

    def _result_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:result"

    def _cancel_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:cancel"

    async def worker_alive(self) -> bool:
        if not self.enabled:
            return False
        try:
            redis = await self.connect()
            raw = await redis.get(VIEW_HEARTBEAT_KEY)
            if not raw:
                return False
            data = json.loads(raw)
            ts = float(data.get("ts", 0.0))
            return (time.time() - ts) <= VIEW_HEARTBEAT_STALE_SECONDS
        except Exception:
            log.warning("Remote view worker heartbeat check failed", exc_info=True)
            return False

    async def status(self) -> dict[str, Any]:
        """Return lightweight dedicated-worker telemetry for the admin panel."""
        base: dict[str, Any] = {
            "enabled": self.enabled, "alive": False, "queue_depth": 0,
            "workers": [], "error": None,
        }
        if not self.enabled:
            return base
        try:
            redis = await self.connect()
            try:
                base["queue_depth"] = int(await redis.xlen(VIEW_STREAM))
            except Exception:
                base["queue_depth"] = -1

            workers: list[dict[str, Any]] = []
            pattern = f"{VIEW_REDIS_PREFIX}:worker:*"
            try:
                async for key in redis.scan_iter(match=pattern, count=50):
                    raw = await redis.get(key)
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw)
                        ts = float(item.get("ts", 0.0))
                    except Exception:
                        continue
                    age = max(0.0, time.time() - ts)
                    if age <= VIEW_HEARTBEAT_STALE_SECONDS:
                        item["age_seconds"] = age
                        workers.append(item)
            except Exception:
                log.debug("Could not enumerate dedicated view workers", exc_info=True)

            # Backwards compatibility with v4.3.14: it only wrote the global key.
            if not workers:
                raw = await redis.get(VIEW_HEARTBEAT_KEY)
                if raw:
                    try:
                        item = json.loads(raw)
                        ts = float(item.get("ts", 0.0))
                        age = max(0.0, time.time() - ts)
                        if age <= VIEW_HEARTBEAT_STALE_SECONDS:
                            item["age_seconds"] = age
                            workers.append(item)
                    except Exception:
                        pass

            base["workers"] = workers
            base["alive"] = bool(workers)
            if workers:
                base["active_jobs"] = sum(int(x.get("active_jobs", 0) or 0) for x in workers)
                base["pool_total"] = sum(int(x.get("view_pool", 0) or 0) for x in workers)
                base["browser_total"] = sum(int(x.get("browser_pool", 0) or 0) for x in workers)
                base["rate_total"] = sum(float(x.get("rate_ema", 0.0) or 0.0) for x in workers)
            return base
        except Exception as exc:
            base["error"] = str(exc)[:300]
            return base

    async def fetch(
        self,
        urls: list[str],
        *,
        progress_cb: Callable[[int, int], Awaitable[None] | None] | None = None,
        traffic_priority: str = "scan_inline",
    ) -> dict[str, RemoteViewResult] | None:
        """Return None only when the remote path is unavailable/aborted.

        The caller can then execute the unchanged v4.3.8 local path. A normal
        remote result may still contain per-URL failures exactly like parser.py.
        """
        if not urls or not self.enabled:
            return None
        urls = list(dict.fromkeys(urls))
        if not await self.worker_alive():
            return None

        redis = await self.connect()
        job_id = uuid.uuid4().hex
        payload_key = self._payload_key(job_id)
        progress_key = self._progress_key(job_id)
        result_key = self._result_key(job_id)
        cancel_key = self._cancel_key(job_id)
        payload = {
            "job_id": job_id,
            "urls": urls,
            "priority": traffic_priority,
            "created_at": time.time(),
            "requester": socket.gethostname(),
        }
        pipe = redis.pipeline(transaction=False)
        pipe.set(payload_key, json.dumps(payload, ensure_ascii=False), ex=VIEW_JOB_TTL_SECONDS)
        pipe.hset(progress_key, mapping={"done": 0, "total": len(urls), "state": "queued", "updated_at": time.time()})
        pipe.expire(progress_key, VIEW_JOB_TTL_SECONDS)
        pipe.xadd(VIEW_STREAM, {"job_id": job_id, "queued_at": str(time.time())})
        await pipe.execute()

        log.info("Remote view batch queued job=%s total=%s", job_id[:10], len(urls))
        started = time.monotonic()
        last_done = -1
        heartbeat_missing_since: float | None = None
        try:
            while True:
                raw_result = await redis.get(result_key)
                if raw_result:
                    data = json.loads(raw_result)
                    if data.get("failed"):
                        log.warning(
                            "Remote view batch requested local fallback job=%s error=%s",
                            job_id[:10], str(data.get("error") or "remote worker failed")[:300],
                        )
                        return None
                    out: dict[str, RemoteViewResult] = {}
                    for url, item in (data.get("results") or {}).items():
                        if not isinstance(item, dict):
                            continue
                        value = item.get("views")
                        out[str(url)] = RemoteViewResult(
                            int(value) if isinstance(value, int) or (isinstance(value, str) and value.isdigit()) else None,
                            str(item.get("source") or "remote:unknown"),
                            item.get("raw_text"), item.get("final_url"), item.get("page_title"), item.get("error"),
                        )
                    if progress_cb is not None:
                        maybe = progress_cb(len(urls), len(urls))
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    log.info("Remote view batch complete job=%s total=%s", job_id[:10], len(out))
                    return out

                progress = await redis.hgetall(progress_key)
                if progress:
                    try:
                        done = max(0, min(len(urls), int(progress.get("done", 0))))
                    except Exception:
                        done = 0
                    if done != last_done:
                        last_done = done
                        if progress_cb is not None:
                            maybe = progress_cb(done, len(urls))
                            if asyncio.iscoroutine(maybe):
                                await maybe

                hb_raw = await redis.get(VIEW_HEARTBEAT_KEY)
                heartbeat_ok = False
                if hb_raw:
                    try:
                        hb = json.loads(hb_raw)
                        heartbeat_ok = (time.time() - float(hb.get("ts", 0.0))) <= VIEW_HEARTBEAT_STALE_SECONDS
                    except Exception:
                        heartbeat_ok = False
                if heartbeat_ok:
                    heartbeat_missing_since = None
                elif heartbeat_missing_since is None:
                    heartbeat_missing_since = time.monotonic()
                elif time.monotonic() - heartbeat_missing_since > VIEW_HEARTBEAT_STALE_SECONDS:
                    log.warning("Remote view worker disappeared job=%s; falling back locally", job_id[:10])
                    await redis.set(cancel_key, "1", ex=VIEW_JOB_TTL_SECONDS)
                    return None

                if time.monotonic() - started > VIEW_REMOTE_TIMEOUT_SECONDS:
                    log.warning("Remote view batch timeout job=%s", job_id[:10])
                    await redis.set(cancel_key, "1", ex=VIEW_JOB_TTL_SECONDS)
                    return None
                await asyncio.sleep(VIEW_PROGRESS_POLL_MS / 1000.0)
        except asyncio.CancelledError:
            try:
                await redis.set(cancel_key, "1", ex=VIEW_JOB_TTL_SECONDS)
            except Exception:
                pass
            raise
        except Exception:
            log.warning("Remote view batch failed job=%s; falling back locally", job_id[:10], exc_info=True)
            try:
                await redis.set(cancel_key, "1", ex=VIEW_JOB_TTL_SECONDS)
            except Exception:
                pass
            return None


REMOTE_VIEW_MANAGER = RemoteViewManager()
