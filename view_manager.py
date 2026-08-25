from __future__ import annotations

import asyncio
import json
import logging
import math
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
# v4.8.3: every release gets a fresh ephemeral view runtime. Old streams/jobs
# can expire naturally without being reclaimed by a newly deployed worker fleet.
VIEW_RUNTIME_PREFIX = os.getenv(
    "VIEW_RUNTIME_PREFIX", f"{VIEW_REDIS_PREFIX}:runtime:v4101"
).strip() or f"{VIEW_REDIS_PREFIX}:runtime:v4101"
VIEW_STREAM = f"{VIEW_RUNTIME_PREFIX}:jobs"
VIEW_GROUP = f"{VIEW_RUNTIME_PREFIX}:workers"
VIEW_HEARTBEAT_KEY = f"{VIEW_RUNTIME_PREFIX}:heartbeat"
VIEW_JOB_TTL_SECONDS = _env_int("VIEW_JOB_TTL_SECONDS", 3600, 300, 24 * 3600)
VIEW_RESULT_TTL_SECONDS = _env_int("VIEW_RESULT_TTL_SECONDS", 900, 60, 6 * 3600)
VIEW_REMOTE_TIMEOUT_SECONDS = _env_int("VIEW_REMOTE_TIMEOUT_SECONDS", 1800, 60, 7200)
VIEW_HEARTBEAT_STALE_SECONDS = _env_int("VIEW_HEARTBEAT_STALE_SECONDS", 20, 8, 120)
VIEW_PROGRESS_POLL_MS = _env_int("VIEW_PROGRESS_POLL_MS", 500, 100, 3000)

# v4.3.20 — View Sharding. Large remote batches are split into independent
# Redis jobs so multiple Railway View Worker replicas can process one scan at
# the same time. The exact view extraction algorithm remains inside the same
# known-good worker/parser code.
# v4.9.1 View Speed Fix. Large foreground batches must never silently fall
# back to a single 100+ URL Redis job because an old Railway variable or a
# transient heartbeat/status read disagreed with the real four-replica fleet.
# The queue is safe even with fewer live workers: small shards are simply
# consumed sequentially until replicas recover. Legacy tuning remains available
# only through an explicit opt-in for diagnostics.
_ALLOW_LEGACY_VIEW_SHARD_TUNING = _env_bool("DT_ALLOW_LEGACY_VIEW_SHARD_TUNING", False)
if _ALLOW_LEGACY_VIEW_SHARD_TUNING:
    VIEW_SHARDING_ENABLED = _env_bool("VIEW_SHARDING_ENABLED", True)
    VIEW_SHARD_MIN_URLS = _env_int("VIEW_SHARD_MIN_URLS", 40, 20, 5000)
    VIEW_SHARD_SIZE = _env_int("VIEW_SHARD_SIZE", 18, 8, 1000)
    VIEW_SHARD_MAX_COUNT = _env_int("VIEW_SHARD_MAX_COUNT", 8, 2, 64)
    VIEW_SHARDS_PER_WORKER = _env_int("VIEW_SHARDS_PER_WORKER", 1, 1, 4)
    VIEW_EXPECTED_WORKERS = _env_int("VIEW_EXPECTED_WORKERS", 4, 1, 16)
else:
    VIEW_SHARDING_ENABLED = True
    VIEW_SHARD_MIN_URLS = 40
    VIEW_SHARD_SIZE = 18
    VIEW_SHARD_MAX_COUNT = 8
    VIEW_SHARDS_PER_WORKER = 1
    VIEW_EXPECTED_WORKERS = 4


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
        # Local telemetry for the admin panel. This is intentionally not stored
        # in Redis because only the main bot creates/shards view batches.
        self.last_shard_count = 0
        self.last_shard_total = 0
        self.last_shard_workers = 0
        self.last_shard_at = 0.0
        self.last_shard_failed = 0
        self.partial_shard_fallbacks_total = 0

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
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:payload"

    def _progress_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:progress"

    def _result_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:result"

    def _cancel_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:cancel"

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
            "sharding_enabled": VIEW_SHARDING_ENABLED,
            "shard_min_urls": VIEW_SHARD_MIN_URLS,
            "shard_size": VIEW_SHARD_SIZE,
            "shard_max_count": VIEW_SHARD_MAX_COUNT,
            "shards_per_worker": VIEW_SHARDS_PER_WORKER,
            "last_shard_count": self.last_shard_count,
            "last_shard_total": self.last_shard_total,
            "last_shard_workers": self.last_shard_workers,
            "last_shard_at": self.last_shard_at,
            "last_shard_failed": self.last_shard_failed,
            "partial_shard_fallbacks_total": self.partial_shard_fallbacks_total,
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
            pattern = f"{VIEW_RUNTIME_PREFIX}:worker:*"
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

    @staticmethod
    def _split_balanced(urls: list[str], shard_count: int) -> list[list[str]]:
        """Split a batch into similarly-sized interleaved shards.

        Interleaving is deliberate: URLs often arrive grouped by category/page.
        Round-robin distribution prevents one shard from accidentally receiving
        most of the slow/browser-fallback URLs while the other replica goes idle.
        """
        shard_count = max(1, min(int(shard_count), len(urls)))
        shards = [urls[index::shard_count] for index in range(shard_count)]
        return [shard for shard in shards if shard]

    async def _fetch_single_batch(
        self,
        urls: list[str],
        *,
        progress_cb: Callable[[int, int], Awaitable[None] | None] | None = None,
        traffic_priority: str = "scan_inline",
        parent_job_id: str | None = None,
        shard_index: int | None = None,
        shard_count: int | None = None,
        skip_alive_check: bool = False,
    ) -> dict[str, RemoteViewResult] | None:
        """Queue and wait for one Redis view job.

        This is the original v4.3.19 remote-job contract, kept intact and moved
        behind a helper so the public fetch() method can fan a large batch out to
        several workers without changing the worker/parser extraction logic.
        """
        if not urls or not self.enabled:
            return None
        urls = list(dict.fromkeys(urls))
        if not skip_alive_check and not await self.worker_alive():
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
        if parent_job_id:
            payload["parent_job_id"] = parent_job_id
        if shard_index is not None:
            payload["shard_index"] = int(shard_index)
        if shard_count is not None:
            payload["shard_count"] = int(shard_count)

        pipe = redis.pipeline(transaction=False)
        pipe.set(payload_key, json.dumps(payload, ensure_ascii=False), ex=VIEW_JOB_TTL_SECONDS)
        pipe.hset(progress_key, mapping={"done": 0, "total": len(urls), "state": "queued", "updated_at": time.time()})
        pipe.expire(progress_key, VIEW_JOB_TTL_SECONDS)
        stream_fields = {"job_id": job_id, "queued_at": str(time.time())}
        if parent_job_id:
            stream_fields["parent_job_id"] = parent_job_id
        if shard_index is not None:
            stream_fields["shard_index"] = str(int(shard_index))
        if shard_count is not None:
            stream_fields["shard_count"] = str(int(shard_count))
        pipe.xadd(VIEW_STREAM, stream_fields)
        await pipe.execute()

        shard_label = ""
        if shard_index is not None and shard_count:
            shard_label = f" shard={int(shard_index) + 1}/{int(shard_count)}"
        log.info("Remote view batch queued job=%s total=%s%s", job_id[:10], len(urls), shard_label)
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
                    log.info("Remote view batch complete job=%s total=%s%s", job_id[:10], len(out), shard_label)
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

    async def fetch(
        self,
        urls: list[str],
        *,
        progress_cb: Callable[[int, int], Awaitable[None] | None] | None = None,
        traffic_priority: str = "scan_inline",
    ) -> dict[str, RemoteViewResult] | None:
        """Fetch exact views remotely, sharding large batches across replicas.

        Safety rules:
        * one worker or a small batch -> original single-job behavior;
        * 2+ healthy workers + a large batch -> several independent Redis jobs;
        * a failed shard no longer cancels healthy shards; completed results are
          preserved and the caller locally retries only the missing URLs.
        """
        if not urls or not self.enabled:
            return None
        urls = list(dict.fromkeys(urls))
        if not await self.worker_alive():
            return None

        # v4.9.1: large jobs are sharded by batch size, not by a momentary
        # heartbeat count. A status read may briefly see one replica during a
        # Railway rollout even though four workers are configured. That must not
        # turn a 149/500 URL scan into one giant worker job. If fewer replicas are
        # actually alive, Redis safely lets them consume the same small shards
        # sequentially.
        worker_count = VIEW_EXPECTED_WORKERS
        if VIEW_SHARDING_ENABLED and len(urls) >= VIEW_SHARD_MIN_URLS:
            try:
                live_status = await self.status()
                live_workers = len(live_status.get("workers") or [])
                worker_count = max(VIEW_EXPECTED_WORKERS, live_workers or 0)
            except Exception:
                worker_count = VIEW_EXPECTED_WORKERS

        should_shard = VIEW_SHARDING_ENABLED and len(urls) >= VIEW_SHARD_MIN_URLS
        if not should_shard:
            self.last_shard_count = 1
            self.last_shard_total = len(urls)
            self.last_shard_workers = worker_count
            self.last_shard_at = time.time()
            self.last_shard_failed = 0
            return await self._fetch_single_batch(
                urls,
                progress_cb=progress_cb,
                traffic_priority=traffic_priority,
                skip_alive_check=True,
            )

        natural_shards = max(2, math.ceil(len(urls) / VIEW_SHARD_SIZE))
        worker_shards = max(2, worker_count * VIEW_SHARDS_PER_WORKER)
        shard_count = min(VIEW_SHARD_MAX_COUNT, len(urls), max(natural_shards, worker_shards))
        shards = self._split_balanced(urls, shard_count)
        shard_count = len(shards)
        parent_job_id = uuid.uuid4().hex

        self.last_shard_count = shard_count
        self.last_shard_total = len(urls)
        self.last_shard_workers = worker_count
        self.last_shard_at = time.time()
        self.last_shard_failed = 0

        log.info(
            "Remote view sharding parent=%s total=%s workers=%s shards=%s target_size=%s",
            parent_job_id[:10], len(urls), worker_count, shard_count, VIEW_SHARD_SIZE,
        )

        progress_lock = asyncio.Lock()
        shard_done = [0 for _ in shards]
        last_combined_done = -1

        async def report_shard_progress(index: int, done: int, shard_total: int) -> None:
            nonlocal last_combined_done
            if progress_cb is None:
                return
            async with progress_lock:
                shard_done[index] = max(0, min(int(done), int(shard_total)))
                combined_done = min(len(urls), sum(shard_done))
                if combined_done == last_combined_done:
                    return
                last_combined_done = combined_done
                maybe = progress_cb(combined_done, len(urls))
                if asyncio.iscoroutine(maybe):
                    await maybe

        def shard_progress_cb(index: int):
            async def _cb(done: int, total: int) -> None:
                await report_shard_progress(index, done, total)
            return _cb

        tasks: list[asyncio.Task] = []
        task_index: dict[asyncio.Task, int] = {}
        for index, shard in enumerate(shards):
            task = asyncio.create_task(
                self._fetch_single_batch(
                    shard,
                    progress_cb=shard_progress_cb(index),
                    traffic_priority=traffic_priority,
                    parent_job_id=parent_job_id,
                    shard_index=index,
                    shard_count=shard_count,
                    skip_alive_check=True,
                ),
                name=f"view-shard-{parent_job_id[:8]}-{index + 1}",
            )
            tasks.append(task)
            task_index[task] = index

        shard_results: list[dict[str, RemoteViewResult] | None] = [None] * shard_count
        failed_shards = 0
        pending: set[asyncio.Task] = set(tasks)
        try:
            while pending:
                done_tasks, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done_tasks:
                    index = task_index[task]
                    try:
                        result = task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.warning(
                            "Remote view shard crashed parent=%s shard=%s/%s; using local fallback",
                            parent_job_id[:10], index + 1, shard_count, exc_info=True,
                        )
                        result = None
                    if result is None:
                        # v4.4.0 partial shard recovery. A single slow/refused shard
                        # must not discard every completed remote shard. Let the
                        # remaining workers finish; the caller locally retries only
                        # URLs missing from the merged result.
                        failed_shards += 1
                        self.partial_shard_fallbacks_total += 1
                        log.warning(
                            "Remote view shard failed parent=%s shard=%s/%s; preserving healthy shards",
                            parent_job_id[:10], index + 1, shard_count,
                        )
                        continue
                    shard_results[index] = result
        except asyncio.CancelledError:
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        merged: dict[str, RemoteViewResult] = {}
        for result in shard_results:
            if result:
                merged.update(result)
        # Restore original URL order for deterministic downstream behavior.
        ordered = {url: merged[url] for url in urls if url in merged}
        # Publish telemetry only after this batch finishes. Keep the functional
        # failure count local so four simultaneous user scans cannot overwrite
        # each other's progress decisions through shared manager fields.
        self.last_shard_failed = failed_shards
        if progress_cb is not None and failed_shards == 0 and last_combined_done != len(urls):
            maybe = progress_cb(len(urls), len(urls))
            if asyncio.iscoroutine(maybe):
                await maybe
        log.info(
            "Remote view sharding complete parent=%s shards=%s failed_shards=%s results=%s/%s",
            parent_job_id[:10], shard_count, failed_shards, len(ordered), len(urls),
        )
        return ordered


REMOTE_VIEW_MANAGER = RemoteViewManager()
