from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any


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


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


# v4.4.0 ignores stale safety-critical worker tuning unless explicitly opted in.
# This prevents old Railway variables from silently undoing the four-replica
# fleet guard after a deployment.
_ALLOW_LEGACY_TUNING = _env_bool("DT_ALLOW_LEGACY_WORKER_TUNING", False)
if not _ALLOW_LEGACY_TUNING:
    for _name, _value in {
        "VIEW_WORKER_POOL_MIN": "4",
        "VIEW_WORKER_POOL_DEFAULT": "5",
        "VIEW_WORKER_POOL_MAX": "6",
        "VIEW_WORKER_BROWSER_POOL_SIZE": "1",
        "VIEW_WORKER_ROUND_SIZE": "24",
        "VIEW_WORKER_MAX_ACTIVE_JOBS": "1",
        "VIEW_WORKER_ROUND_TIMEOUT_SECONDS": "120",
        "VIEW_JOB_TIMEOUT_SECONDS": "120",
    }.items():
        os.environ[_name] = _value

# ---------------------------------------------------------------------------
# v4.3.18 DUAL VIEW WORKER
# ---------------------------------------------------------------------------
# This process owns ONLY public view counters. parser.py is the known-good
# v4.3.8 core and is intentionally not edited. We configure the process-wide
# traffic controller before importing parser.py, then adjust only its exposed
# limits from this worker as the dedicated lane learns the healthy capacity.
VIEW_POOL_MIN = _env_int("VIEW_WORKER_POOL_MIN", 4, 2, 24)
VIEW_POOL_MAX = _env_int("VIEW_WORKER_POOL_MAX", 6, VIEW_POOL_MIN, 24)
VIEW_POOL_DEFAULT = _env_int("VIEW_WORKER_POOL_DEFAULT", 5, VIEW_POOL_MIN, VIEW_POOL_MAX)
# Backwards compatibility with v4.3.14 env. If the new DEFAULT is absent but
# VIEW_WORKER_VIEW_POOL_SIZE exists, use the old value as the starting pool.
if "VIEW_WORKER_POOL_DEFAULT" not in os.environ and "VIEW_WORKER_VIEW_POOL_SIZE" in os.environ:
    VIEW_POOL_DEFAULT = _env_int("VIEW_WORKER_VIEW_POOL_SIZE", VIEW_POOL_DEFAULT, VIEW_POOL_MIN, VIEW_POOL_MAX)

BROWSER_POOL = _env_int("VIEW_WORKER_BROWSER_POOL_SIZE", 1, 1, 4)
VIEW_INTERVAL = _env_float("VIEW_WORKER_VIEW_MIN_INTERVAL_SECONDS", 0.05, 0.0, 2.0)
ADAPTIVE_ENABLED = _env_bool("VIEW_WORKER_ADAPTIVE_ENABLED", True)
ADAPTIVE_HEALTHY_ROUNDS = _env_int("VIEW_WORKER_ADAPTIVE_HEALTHY_ROUNDS", 2, 1, 20)
ADAPTIVE_BACKOFF_SECONDS = _env_float("VIEW_WORKER_ADAPTIVE_BACKOFF_SECONDS", 3.0, 1.0, 120.0)
ADAPTIVE_FALLBACK_WARN_RATIO = _env_float("VIEW_WORKER_ADAPTIVE_FALLBACK_WARN_RATIO", 0.12, 0.01, 1.0)
ADAPTIVE_UNKNOWN_WARN_RATIO = _env_float("VIEW_WORKER_ADAPTIVE_UNKNOWN_WARN_RATIO", 0.05, 0.0, 1.0)

# v4.9.1 FOUR-USER VIEW FLEET SPEED GUARD.
# Four Railway View Worker replicas share one Redis traffic budget. Official
# HTTP counters stay capped at 16 fleet-wide. Chromium remains one-at-a-time
# inside each replica, but up to two different replicas may run a verified
# fallback concurrently so a handful of transient misses cannot serialize the
# entire scan for minutes.
# Main bot / Date / Page workers keep their existing proven traffic modes.
os.environ["STABLE_SINGLE_SERVICE_MODE"] = "0"
os.environ["DIST_TRAFFIC_VIEW_BUCKET"] = "view"
os.environ["DIST_TRAFFIC_BROWSER_BUCKET"] = "view-browser"
os.environ["DIST_TRAFFIC_GLOBAL_BUCKET"] = "view-fleet"
os.environ["DIST_TRAFFIC_COOLDOWN_BUCKET"] = "view-fleet"
os.environ["DIST_TRAFFIC_VIEW_LIMIT"] = "16"
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = "16"
os.environ["DIST_TRAFFIC_BROWSER_LIMIT"] = "2"

os.environ["DIST_TRAFFIC_SHARED_COOLDOWN"] = "0"
os.environ["TRAFFIC_MAX_PENALTY_LEVEL"] = "1"
os.environ["TRAFFIC_403_COOLDOWN_SECONDS"] = "0"
os.environ["TRAFFIC_429_COOLDOWN_SECONDS"] = "3"
os.environ["TRAFFIC_MAX_COOLDOWN_SECONDS"] = "3"
os.environ["TRAFFIC_RECOVERY_SUCCESS_COUNT"] = "10"
os.environ["TRAFFIC_RECOVERY_QUIET_SECONDS"] = "10"

# The TrafficManager is created during parser import. Give it the physical MAX;
# the worker later lowers base_view_limit to current_pool before every round.
os.environ["TRAFFIC_VIEW_CONCURRENCY"] = str(VIEW_POOL_MAX)
os.environ["TRAFFIC_BROWSER_CONCURRENCY"] = str(BROWSER_POOL)
os.environ["TRAFFIC_GLOBAL_CONCURRENCY"] = str(max(VIEW_POOL_MAX + BROWSER_POOL, 14))
os.environ["TRAFFIC_VIEW_MIN_INTERVAL_SECONDS"] = str(VIEW_INTERVAL)
os.environ["TRAFFIC_BACKGROUND_VIEWS_DURING_SCANS"] = "0"
os.environ["ACCURATE_VIEW_HTTP_CONCURRENCY"] = str(min(24, max(VIEW_POOL_MAX, VIEW_POOL_MAX * 2)))
os.environ["ACCURATE_VIEW_BROWSER_CONCURRENCY"] = str(BROWSER_POOL)
os.environ.setdefault("SHARED_BROWSER_RUNTIME", "1")

try:
    from redis.asyncio import Redis  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("redis package is required for view worker") from exc

from app_version import APP_VERSION
from parser import KleinanzeigenParser, shutdown_shared_browser_runtime
from traffic import TRAFFIC
from view_manager import (
    REDIS_URL,
    VIEW_GROUP,
    VIEW_HEARTBEAT_KEY,
    VIEW_JOB_TTL_SECONDS,
    VIEW_REDIS_PREFIX,
    VIEW_RUNTIME_PREFIX,
    VIEW_RESULT_TTL_SECONDS,
    VIEW_STREAM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-view-counter-worker")

ROUND_SIZE = _env_int("VIEW_WORKER_ROUND_SIZE", 24, VIEW_POOL_MAX, 200)
MAX_ACTIVE_JOBS = _env_int("VIEW_WORKER_MAX_ACTIVE_JOBS", 1, 1, 16)
BLOCK_MS = _env_int("VIEW_WORKER_QUEUE_BLOCK_MS", 250, 50, 2000)
HEARTBEAT_SECONDS = _env_float("VIEW_WORKER_HEARTBEAT_SECONDS", 3.0, 1.0, 10.0)
RECLAIM_IDLE_MS = _env_int("VIEW_WORKER_RECLAIM_IDLE_MS", 120_000, 30_000, 30 * 60_000)
ROUND_TIMEOUT_SECONDS = _env_int("VIEW_WORKER_ROUND_TIMEOUT_SECONDS", 120, 30, 900)
JOB_STALL_SECONDS = _env_int("VIEW_JOB_TIMEOUT_SECONDS", 120, 60, 1800)
JOB_REQUEUE_ENABLED = _env_bool("VIEW_JOB_REQUEUE_ENABLED", True)
MAX_REQUEUES = _env_int("VIEW_WORKER_MAX_REQUEUES", 2, 0, 10)
ROUND_FAILURES_BEFORE_RESET = _env_int("VIEW_WORKER_FAILURES_BEFORE_RESET", 2, 1, 10)
STATUS_TTL_SECONDS = max(10, int(HEARTBEAT_SECONDS * 5))


@dataclass
class ActiveJob:
    job_id: str
    message_id: str
    urls: deque[str]
    total: int
    priority: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    last_progress_at: float = field(default_factory=time.monotonic)
    round_failures: int = 0
    requeues: int = 0


class ViewCounterWorker:
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
        self.consumer = f"view-{socket.gethostname()}-{os.getpid()}"
        self.active: OrderedDict[str, ActiveJob] = OrderedDict()
        self.parser = KleinanzeigenParser()
        self.current_pool = VIEW_POOL_DEFAULT
        self.healthy_rounds = 0
        self.growth_blocked_until = 0.0
        self._group_ready = False
        self._last_hb = 0.0
        self._started_at = time.monotonic()
        self._processing_round = False
        self._stop = asyncio.Event()

        # Rolling telemetry. These are diagnostics only and never influence the
        # exact value stored for an ad.
        self.rounds_total = 0
        self.rounds_failed = 0
        self.requeues_total = 0
        self.processed_total = 0
        self.exact_total = 0
        self.official_total = 0
        self.browser_total = 0
        self.unknown_total = 0
        self.refusal_counts = {403: 0, 429: 0}
        self.rate_ema = 0.0
        self.item_ms_ema = 0.0
        self.last_round_rate = 0.0
        self.last_round_seconds = 0.0
        self.last_adaptive_reason = "startup"

        # Count 403/429 without modifying parser.py/traffic.py. parser.py uses the
        # same process-wide TRAFFIC object, so this tiny wrapper only observes the
        # existing refusal report and then calls the original implementation.
        original_report_refusal = TRAFFIC.report_refusal

        async def tracked_refusal(status_code: int, kind: str) -> None:
            code = int(status_code)
            if code in self.refusal_counts:
                self.refusal_counts[code] += 1
            await original_report_refusal(code, kind)

        TRAFFIC.report_refusal = tracked_refusal  # type: ignore[method-assign]
        self._apply_pool_limit()

    def payload_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:payload"

    def progress_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:progress"

    def result_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:result"

    def cancel_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:cancel"

    def partial_key(self, job_id: str) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:job:{job_id}:partial"

    def worker_status_key(self) -> str:
        return f"{VIEW_RUNTIME_PREFIX}:worker:{self.consumer}"

    def _apply_pool_limit(self) -> None:
        # parser.py's HTTP phase has its own wide semaphore, but every request
        # still leases TRAFFIC. Changing these exposed base limits therefore gives
        # us a real dynamic pool without touching the known-good parser core.
        TRAFFIC.base_view_limit = int(self.current_pool)
        TRAFFIC.base_global_limit = max(int(self.current_pool) + BROWSER_POOL, 4)

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.redis.xgroup_create(VIEW_STREAM, VIEW_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc).upper():
                raise
        self._group_ready = True

    async def _touch_active_claims(self) -> None:
        """Refresh pending-idle timers so a healthy long job is not stolen.

        XAUTOCLAIM remains the crash-recovery path. This touch makes future
        multi-replica deployment safe even when a round lasts longer than the
        reclaim-idle threshold.
        """
        ids = [job.message_id for job in self.active.values() if job.message_id]
        if not ids:
            return
        try:
            await self.redis.xclaim(VIEW_STREAM, VIEW_GROUP, self.consumer, 0, ids, justid=True)
        except Exception:
            # Older Redis/redis-py combinations can differ in XCLAIM options. The
            # worker remains fully functional; XAUTOCLAIM still recovers crashes.
            log.debug("Could not refresh active Redis stream claims", exc_info=True)

    async def heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_hb < HEARTBEAT_SECONDS:
            return
        self._last_hb = now
        self._apply_pool_limit()
        try:
            queue_depth = int(await self.redis.xlen(VIEW_STREAM))
        except Exception:
            queue_depth = -1
        try:
            traffic = await TRAFFIC.snapshot()
            traffic_view_limit = int(traffic.view_limit)
            penalty = int(traffic.penalty_level)
            cooldown = float(traffic.cooldown_seconds)
            refusals_60s = int(traffic.refusals_60s)
        except Exception:
            traffic_view_limit = self.current_pool
            penalty = 0
            cooldown = 0.0
            refusals_60s = 0

        payload = {
            "ts": time.time(),
            "consumer": self.consumer,
            "version": APP_VERSION,
            "view_pool": self.current_pool,
            "pool_min": VIEW_POOL_MIN,
            "pool_default": VIEW_POOL_DEFAULT,
            "pool_max": VIEW_POOL_MAX,
            "traffic_view_limit": traffic_view_limit,
            "browser_pool": BROWSER_POOL,
            "fleet_view_limit": int(os.environ.get("DIST_TRAFFIC_VIEW_LIMIT", "16")),
            "fleet_global_limit": int(os.environ.get("DIST_TRAFFIC_GLOBAL_LIMIT", "16")),
            "fleet_bucket": os.environ.get("DIST_TRAFFIC_GLOBAL_BUCKET", "view-fleet"),
            "active_jobs": len(self.active),
            "round_size": ROUND_SIZE,
            "processing_round": self._processing_round,
            "queue_depth": queue_depth,
            "rate_ema": round(self.rate_ema, 3),
            "last_round_rate": round(self.last_round_rate, 3),
            "item_ms_ema": round(self.item_ms_ema, 1),
            "processed_total": self.processed_total,
            "exact_total": self.exact_total,
            "official_total": self.official_total,
            "browser_total": self.browser_total,
            "unknown_total": self.unknown_total,
            "exact_pct": round((100.0 * self.exact_total / self.processed_total), 2) if self.processed_total else 0.0,
            "fallback_pct": round((100.0 * self.browser_total / self.processed_total), 2) if self.processed_total else 0.0,
            "http_403": self.refusal_counts[403],
            "http_429": self.refusal_counts[429],
            "penalty": penalty,
            "cooldown_seconds": round(cooldown, 2),
            "refusals_60s": refusals_60s,
            "rounds_total": self.rounds_total,
            "rounds_failed": self.rounds_failed,
            "requeues_total": self.requeues_total,
            "adaptive_reason": self.last_adaptive_reason,
            "uptime_seconds": int(now - self._started_at),
        }
        raw = json.dumps(payload, ensure_ascii=False)
        pipe = self.redis.pipeline(transaction=False)
        # Global key: enough for the bot to know at least one worker is healthy.
        pipe.set(VIEW_HEARTBEAT_KEY, raw, ex=STATUS_TTL_SECONDS)
        # Per-consumer key: admin telemetry + future multiple Railway replicas.
        pipe.set(self.worker_status_key(), raw, ex=STATUS_TTL_SECONDS)
        await pipe.execute()
        await self._touch_active_claims()

    async def heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.heartbeat(force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Dedicated view heartbeat failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _load_partial_results(self, job_id: str) -> dict[str, dict[str, Any]]:
        try:
            raw = await self.redis.hgetall(self.partial_key(job_id))
        except Exception:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for url, value in (raw or {}).items():
            try:
                item = json.loads(value)
                if isinstance(item, dict):
                    out[str(url)] = item
            except Exception:
                continue
        return out

    async def _fail_stream_message_for_local_fallback(self, msg_id: str, job_id: str, reason: str) -> None:
        """Finish a malformed Redis message so Parser falls back immediately.

        A stream entry without a usable payload must never be merely ACKed: the
        manager would keep polling its result key until the remote timeout. Publish
        the same explicit failed marker used for an admitted ActiveJob instead.
        """
        pipe = self.redis.pipeline(transaction=False)
        if job_id:
            payload = {
                "job_id": job_id,
                "failed": True,
                "error": reason[:500],
                "completed_at": time.time(),
            }
            pipe.set(self.result_key(job_id), json.dumps(payload, ensure_ascii=False), ex=VIEW_RESULT_TTL_SECONDS)
            pipe.hset(
                self.progress_key(job_id),
                mapping={"state": "remote_failed", "updated_at": time.time()},
            )
            pipe.expire(self.progress_key(job_id), VIEW_RESULT_TTL_SECONDS)
            pipe.delete(self.partial_key(job_id))
        pipe.xack(VIEW_STREAM, VIEW_GROUP, msg_id)
        pipe.xdel(VIEW_STREAM, msg_id)
        await pipe.execute()
        log.error("View stream message handed back to local fallback job=%s reason=%s", job_id[:10], reason)

    async def _load_message(self, msg_id: str, fields: dict[str, Any]) -> bool:
        job_id = str((fields or {}).get("job_id") or "")
        if not job_id or job_id in self.active:
            if not job_id:
                # There is no manager-visible job id to signal. Remove the corrupt
                # stream entry completely so it cannot be reclaimed forever.
                pipe = self.redis.pipeline(transaction=False)
                pipe.xack(VIEW_STREAM, VIEW_GROUP, msg_id)
                pipe.xdel(VIEW_STREAM, msg_id)
                await pipe.execute()
            return False
        raw = await self.redis.get(self.payload_key(job_id))
        if not raw:
            await self._fail_stream_message_for_local_fallback(msg_id, job_id, "view payload missing")
            return False
        try:
            payload = json.loads(raw)
            all_urls = list(dict.fromkeys(str(x) for x in payload.get("urls", []) if isinstance(x, str) and x))
        except Exception:
            all_urls = []
            payload = {}
        if not all_urls:
            await self._fail_stream_message_for_local_fallback(msg_id, job_id, "view payload invalid or empty")
            return False

        partial = await self._load_partial_results(job_id)
        remaining = [url for url in all_urls if url not in partial]
        try:
            requeues = int(payload.get("requeues", 0) or 0)
        except Exception:
            requeues = 0
        job = ActiveJob(
            job_id=job_id,
            message_id=str(msg_id),
            urls=deque(remaining),
            total=len(all_urls),
            priority=str(payload.get("priority") or "scan_inline"),
            results=partial,
            requeues=requeues,
        )
        self.active[job_id] = job
        done = len(partial)
        await self.redis.hset(
            self.progress_key(job_id),
            mapping={"done": done, "total": job.total, "state": "running", "updated_at": time.time()},
        )
        await self.redis.expire(self.progress_key(job_id), VIEW_JOB_TTL_SECONDS)
        log.info(
            "View job admitted job=%s total=%s resumed=%s active_jobs=%s",
            job_id[:10], job.total, done, len(self.active),
        )
        return True

    async def admit_jobs(self) -> None:
        await self.ensure_group()
        while len(self.active) < MAX_ACTIVE_JOBS:
            # v4.8.3: new view shards win over crash recovery. A stale pending
            # message can never delay the user's current scan after a deployment.
            rows = await self.redis.xreadgroup(
                VIEW_GROUP,
                self.consumer,
                {VIEW_STREAM: ">"},
                count=1,
                block=BLOCK_MS if not self.active else 1,
            )
            if rows and rows[0][1]:
                msg_id, fields = rows[0][1][0]
                if await self._load_message(str(msg_id), fields or {}):
                    continue

            loaded = False
            try:
                claimed = await self.redis.xautoclaim(
                    VIEW_STREAM,
                    VIEW_GROUP,
                    self.consumer,
                    min_idle_time=RECLAIM_IDLE_MS,
                    start_id="0-0",
                    count=1,
                )
                messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
                if messages:
                    msg_id, fields = messages[0]
                    loaded = await self._load_message(str(msg_id), fields or {})
            except Exception:
                log.debug("View worker XAUTOCLAIM skipped", exc_info=True)
            if not loaded:
                break

    async def _persist_partial(self, job: ActiveJob, url: str, item: dict[str, Any]) -> None:
        key = self.partial_key(job.job_id)
        pipe = self.redis.pipeline(transaction=False)
        pipe.hset(key, url, json.dumps(item, ensure_ascii=False))
        pipe.expire(key, VIEW_JOB_TTL_SECONDS)
        await pipe.execute()

    async def finish_job(self, job: ActiveJob) -> None:
        payload = {"job_id": job.job_id, "results": job.results, "completed_at": time.time()}
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(self.result_key(job.job_id), json.dumps(payload, ensure_ascii=False), ex=VIEW_RESULT_TTL_SECONDS)
        pipe.hset(
            self.progress_key(job.job_id),
            mapping={"done": job.total, "total": job.total, "state": "done", "updated_at": time.time()},
        )
        pipe.expire(self.progress_key(job.job_id), VIEW_RESULT_TTL_SECONDS)
        pipe.xack(VIEW_STREAM, VIEW_GROUP, job.message_id)
        pipe.xdel(VIEW_STREAM, job.message_id)
        pipe.delete(self.partial_key(job.job_id))
        await pipe.execute()
        elapsed = max(0.001, time.monotonic() - job.started_at)
        good = sum(1 for item in job.results.values() if item.get("views") is not None)
        log.info(
            "View job complete job=%s exact=%s/%s elapsed=%.1fs rate=%.2f/s",
            job.job_id[:10], good, job.total, elapsed, job.total / elapsed,
        )
        self.active.pop(job.job_id, None)

    async def fail_job_for_local_fallback(self, job: ActiveJob, reason: str) -> None:
        """Tell the main bot to use the untouched local v4.3.8 path.

        Returning a normal remote result with hundreds of None values would be a
        false success. A failed marker deliberately causes RemoteViewManager.fetch
        to return None, which activates the existing local fallback.
        """
        payload = {
            "job_id": job.job_id,
            "failed": True,
            "error": reason[:500],
            "completed_at": time.time(),
        }
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(self.result_key(job.job_id), json.dumps(payload, ensure_ascii=False), ex=VIEW_RESULT_TTL_SECONDS)
        pipe.hset(
            self.progress_key(job.job_id),
            mapping={"state": "remote_failed", "updated_at": time.time()},
        )
        pipe.expire(self.progress_key(job.job_id), VIEW_RESULT_TTL_SECONDS)
        pipe.xack(VIEW_STREAM, VIEW_GROUP, job.message_id)
        pipe.xdel(VIEW_STREAM, job.message_id)
        pipe.delete(self.partial_key(job.job_id))
        await pipe.execute()
        self.active.pop(job.job_id, None)
        log.error("View job handed back to local fallback job=%s reason=%s", job.job_id[:10], reason)

    async def cancel_job(self, job: ActiveJob) -> None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.hset(self.progress_key(job.job_id), mapping={"state": "cancelled", "updated_at": time.time()})
        pipe.expire(self.progress_key(job.job_id), VIEW_RESULT_TTL_SECONDS)
        pipe.xack(VIEW_STREAM, VIEW_GROUP, job.message_id)
        pipe.xdel(VIEW_STREAM, job.message_id)
        pipe.delete(self.partial_key(job.job_id))
        await pipe.execute()
        self.active.pop(job.job_id, None)
        log.info("View job cancelled job=%s", job.job_id[:10])

    async def requeue_job(self, job: ActiveJob, reason: str) -> None:
        if not JOB_REQUEUE_ENABLED or job.requeues >= MAX_REQUEUES:
            await self.fail_job_for_local_fallback(job, f"requeue exhausted: {reason}")
            return
        job.requeues += 1
        self.requeues_total += 1
        try:
            raw = await self.redis.get(self.payload_key(job.job_id))
            if not raw:
                await self.fail_job_for_local_fallback(job, f"requeue payload missing: {reason}")
                return
            payload = json.loads(raw)
            urls = payload.get("urls") if isinstance(payload, dict) else None
            if not isinstance(urls, list) or not urls:
                await self.fail_job_for_local_fallback(job, f"requeue payload invalid: {reason}")
                return
            payload["requeues"] = job.requeues
            payload["last_requeue_at"] = time.time()
            payload["last_requeue_reason"] = reason[:300]
        except Exception:
            await self.fail_job_for_local_fallback(job, f"requeue payload unreadable: {reason}")
            return
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(self.payload_key(job.job_id), json.dumps(payload, ensure_ascii=False), ex=VIEW_JOB_TTL_SECONDS)
        pipe.hset(
            self.progress_key(job.job_id),
            mapping={
                "done": len(job.results),
                "total": job.total,
                "state": "requeued",
                "updated_at": time.time(),
            },
        )
        pipe.expire(self.progress_key(job.job_id), VIEW_JOB_TTL_SECONDS)
        pipe.xack(VIEW_STREAM, VIEW_GROUP, job.message_id)
        pipe.xdel(VIEW_STREAM, job.message_id)
        pipe.xadd(VIEW_STREAM, {"job_id": job.job_id, "requeued_at": str(time.time())})
        await pipe.execute()
        self.active.pop(job.job_id, None)
        log.warning(
            "View job requeued job=%s done=%s/%s attempt=%s reason=%s",
            job.job_id[:10], len(job.results), job.total, job.requeues, reason,
        )

    async def requeue_stalled_jobs(self) -> None:
        now = time.monotonic()
        for job in list(self.active.values()):
            if not job.urls:
                continue
            if now - job.last_progress_at >= JOB_STALL_SECONDS:
                await self.requeue_job(job, f"no progress for {JOB_STALL_SECONDS}s")

    def fair_round(self) -> list[tuple[ActiveJob, str]]:
        jobs = [job for job in self.active.values() if job.urls]
        if not jobs:
            return []
        selected: list[tuple[ActiveJob, str]] = []
        # Fair interleaving: one URL per active scan in each pass. A single scan
        # can still consume the whole dedicated worker when nobody else waits.
        target = max(self.current_pool, min(ROUND_SIZE, self.current_pool * 4))
        while len(selected) < target and any(job.urls for job in jobs):
            for job in jobs:
                if len(selected) >= target:
                    break
                if job.urls:
                    selected.append((job, job.urls.popleft()))
        return selected

    @staticmethod
    def serialize_result(vr) -> dict[str, Any]:
        return {
            "views": int(vr.views) if vr.views is not None else None,
            "raw_text": (vr.raw_text[:400] if isinstance(vr.raw_text, str) else None),
            "source": str(vr.source or "remote:unknown"),
            "final_url": vr.final_url,
            "page_title": (vr.page_title[:200] if isinstance(vr.page_title, str) else None),
            "error": (vr.error[:400] if isinstance(vr.error, str) else None),
        }

    def _update_telemetry_and_pool(
        self,
        *,
        processed: int,
        elapsed: float,
        official: int,
        browser: int,
        unknown: int,
        new_refusals: int,
        penalty_level: int,
    ) -> None:
        self.rounds_total += 1
        self.processed_total += processed
        self.official_total += official
        self.browser_total += browser
        self.unknown_total += unknown
        self.exact_total += max(0, processed - unknown)
        rate = processed / max(0.001, elapsed)
        item_ms = (elapsed * 1000.0) / max(1, processed)
        self.last_round_rate = rate
        self.last_round_seconds = elapsed
        alpha = 0.25
        self.rate_ema = rate if self.rate_ema <= 0 else (self.rate_ema * (1 - alpha) + rate * alpha)
        self.item_ms_ema = item_ms if self.item_ms_ema <= 0 else (self.item_ms_ema * (1 - alpha) + item_ms * alpha)

        if not ADAPTIVE_ENABLED:
            self.last_adaptive_reason = "adaptive disabled"
            return
        fallback_ratio = browser / max(1, processed)
        unknown_ratio = unknown / max(1, processed)
        now = time.monotonic()
        if new_refusals > 0 or penalty_level > 0:
            old = self.current_pool
            self.current_pool = max(VIEW_POOL_MIN, self.current_pool - 1)
            self.healthy_rounds = 0
            self.growth_blocked_until = now + ADAPTIVE_BACKOFF_SECONDS
            self.last_adaptive_reason = f"refusal/local-soft: {old}->{self.current_pool}"
        elif fallback_ratio >= ADAPTIVE_FALLBACK_WARN_RATIO or unknown_ratio >= ADAPTIVE_UNKNOWN_WARN_RATIO:
            old = self.current_pool
            self.current_pool = max(VIEW_POOL_MIN, self.current_pool - 1)
            self.healthy_rounds = 0
            self.growth_blocked_until = now + ADAPTIVE_BACKOFF_SECONDS
            self.last_adaptive_reason = (
                f"fallback {fallback_ratio:.1%}/unknown {unknown_ratio:.1%}: {old}->{self.current_pool}"
            )
        else:
            self.healthy_rounds += 1
            if (
                self.healthy_rounds >= ADAPTIVE_HEALTHY_ROUNDS
                and now >= self.growth_blocked_until
                and self.current_pool < VIEW_POOL_MAX
            ):
                old = self.current_pool
                self.current_pool += 1
                self.healthy_rounds = 0
                self.last_adaptive_reason = f"healthy: {old}->{self.current_pool}"
            else:
                self.last_adaptive_reason = f"healthy {self.healthy_rounds}/{ADAPTIVE_HEALTHY_ROUNDS}"
        self._apply_pool_limit()

    async def reset_parser(self, reason: str) -> None:
        log.warning("Resetting dedicated view parser reason=%s", reason)
        try:
            await self.parser.close()
        except Exception:
            log.debug("View parser close during reset failed", exc_info=True)
        self.parser = KleinanzeigenParser()

    async def process_round(self) -> None:
        selected = self.fair_round()
        if not selected:
            for job in list(self.active.values()):
                if not job.urls and len(job.results) >= job.total:
                    await self.finish_job(job)
            return

        urls = [url for _, url in selected]
        self._apply_pool_limit()
        before = await TRAFFIC.snapshot()
        started = time.monotonic()
        self._processing_round = True
        try:
            results = await self.parser.fetch_public_view_counts(
                urls,
                concurrency=self.current_pool,
                progress_cb=None,
                traffic_priority="normal",
                browser_fallback=True,
                direct_http_only=False,
                accurate=True,
            )
        except asyncio.CancelledError:
            for job, url in reversed(selected):
                job.urls.appendleft(url)
            raise
        except Exception:
            for job, url in reversed(selected):
                job.urls.appendleft(url)
                job.round_failures += 1
            raise
        finally:
            self._processing_round = False

        elapsed = max(0.001, time.monotonic() - started)
        after = await TRAFFIC.snapshot()
        new_refusals = max(0, after.total_refusals - before.total_refusals)
        touched: set[str] = set()
        official = 0
        browser = 0
        unknown = 0
        persist_rows: list[tuple[ActiveJob, str, dict[str, Any]]] = []

        for job, url in selected:
            touched.add(job.job_id)
            vr = results.get(url)
            if vr is None:
                item = {
                    "views": None,
                    "raw_text": None,
                    "source": "remote:missing-result",
                    "final_url": None,
                    "page_title": None,
                    "error": "worker returned no result",
                }
            else:
                item = self.serialize_result(vr)
            source = str(item.get("source") or "")
            if item.get("views") is None:
                unknown += 1
            if source.startswith("verified-official:"):
                official += 1
            elif not source.startswith("verified:invalid-url"):
                # In Accurate Views Core every non-official result reached the
                # browser lane (including verified:not-found after that attempt).
                browser += 1
            job.results[url] = item
            job.round_failures = 0
            job.last_progress_at = time.monotonic()
            persist_rows.append((job, url, item))

        # Persist partial work once per round. If Railway restarts this service,
        # XAUTOCLAIM resumes only the remaining URLs instead of repeating the batch.
        if persist_rows:
            pipe = self.redis.pipeline(transaction=False)
            touched_partial: set[str] = set()
            for job, url, item in persist_rows:
                key = self.partial_key(job.job_id)
                touched_partial.add(key)
                pipe.hset(key, url, json.dumps(item, ensure_ascii=False))
            for key in touched_partial:
                pipe.expire(key, VIEW_JOB_TTL_SECONDS)
            await pipe.execute()

        for job_id in touched:
            job = self.active.get(job_id)
            if job is None:
                continue
            done = len(job.results)
            await self.redis.hset(
                self.progress_key(job_id),
                mapping={"done": done, "total": job.total, "state": "running", "updated_at": time.time()},
            )
            await self.redis.expire(self.progress_key(job_id), VIEW_JOB_TTL_SECONDS)

        self._update_telemetry_and_pool(
            processed=len(selected),
            elapsed=elapsed,
            official=official,
            browser=browser,
            unknown=unknown,
            new_refusals=new_refusals,
            penalty_level=after.penalty_level,
        )
        log.info(
            "View round done n=%s pool=%s rate=%.2f/s official=%s browser=%s unknown=%s "
            "refusals=%s adaptive=%s",
            len(selected), self.current_pool, self.last_round_rate, official, browser, unknown,
            new_refusals, self.last_adaptive_reason,
        )

        for job in list(self.active.values()):
            if await self.redis.exists(self.cancel_key(job.job_id)):
                await self.cancel_job(job)
            elif not job.urls and len(job.results) >= job.total:
                await self.finish_job(job)

    async def run(self) -> None:
        await self.redis.ping()
        await self.ensure_group()
        await self.heartbeat(force=True)
        hb_task = asyncio.create_task(self.heartbeat_loop(), name="view-worker-heartbeat")
        log.info(
            "DT PARSER dedicated view worker online | consumer=%s pool=%s [%s..%s] browser=%s "
            "round=%s max_jobs=%s adaptive=%s",
            self.consumer,
            self.current_pool,
            VIEW_POOL_MIN,
            VIEW_POOL_MAX,
            BROWSER_POOL,
            ROUND_SIZE,
            MAX_ACTIVE_JOBS,
            ADAPTIVE_ENABLED,
        )
        consecutive_failures = 0
        try:
            while True:
                await self.admit_jobs()
                await self.requeue_stalled_jobs()
                if self.active:
                    try:
                        await asyncio.wait_for(self.process_round(), timeout=ROUND_TIMEOUT_SECONDS)
                        consecutive_failures = 0
                    except asyncio.TimeoutError:
                        self.rounds_failed += 1
                        consecutive_failures += 1
                        self.current_pool = max(VIEW_POOL_MIN, self.current_pool - 2)
                        self.growth_blocked_until = time.monotonic() + ADAPTIVE_BACKOFF_SECONDS
                        self.last_adaptive_reason = f"round timeout -> {self.current_pool}"
                        self._apply_pool_limit()
                        log.error("Dedicated view round timeout after %ss", ROUND_TIMEOUT_SECONDS)
                    except Exception:
                        self.rounds_failed += 1
                        consecutive_failures += 1
                        self.current_pool = max(VIEW_POOL_MIN, self.current_pool - 2)
                        self.growth_blocked_until = time.monotonic() + ADAPTIVE_BACKOFF_SECONDS
                        self.last_adaptive_reason = f"round error -> {self.current_pool}"
                        self._apply_pool_limit()
                        log.exception("Dedicated view round failed")
                    if consecutive_failures >= ROUND_FAILURES_BEFORE_RESET:
                        await self.reset_parser(f"{consecutive_failures} consecutive round failures")
                        consecutive_failures = 0
                    if consecutive_failures:
                        await asyncio.sleep(min(8.0, 0.75 * (2 ** min(consecutive_failures, 3))))
                else:
                    await asyncio.sleep(0.05)
        finally:
            self._stop.set()
            hb_task.cancel()
            await asyncio.gather(hb_task, return_exceptions=True)
            try:
                await self.parser.close()
            finally:
                await shutdown_shared_browser_runtime()
                try:
                    await self.redis.delete(self.worker_status_key())
                except Exception:
                    pass
                await self.redis.aclose()


async def main() -> None:
    worker = ViewCounterWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
