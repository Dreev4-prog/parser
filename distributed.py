from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any

log = logging.getLogger("dtparser-distributed")

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover - local tests may not install optional redis dep
    Redis = None  # type: ignore


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
DISTRIBUTED_WORKERS = _env_bool("DISTRIBUTED_WORKERS", False) and bool(REDIS_URL)
REDIS_PREFIX = os.getenv("REDIS_PREFIX", "dtparser").strip() or "dtparser"
STREAM_NAME = f"{REDIS_PREFIX}:scan_jobs"
STREAM_GROUP = f"{REDIS_PREFIX}:parser_workers"
CATEGORY_LOCK_SECONDS = _env_int("CATEGORY_LOCK_SECONDS", 1800, 60, 7200)
CATEGORY_PROGRESS_TTL_SECONDS = _env_int("CATEGORY_PROGRESS_TTL_SECONDS", 90, 15, 600)
JOB_MARKER_TTL_SECONDS = _env_int("JOB_MARKER_TTL_SECONDS", 7 * 24 * 3600, 3600, 30 * 24 * 3600)
WORKER_HEARTBEAT_TTL_SECONDS = _env_int("WORKER_HEARTBEAT_TTL_SECONDS", 20, 10, 120)
JOB_LOCK_SECONDS = _env_int("JOB_LOCK_SECONDS", 45, 20, 300)
PENDING_RECLAIM_IDLE_MS = _env_int("PENDING_RECLAIM_IDLE_MS", 60_000, 10_000, 30 * 60_000)
DIST_TRAFFIC_SCAN_LIMIT = _env_int("DIST_TRAFFIC_SCAN_LIMIT", 5, 1, 20)
DIST_TRAFFIC_VIEW_LIMIT = _env_int("DIST_TRAFFIC_VIEW_LIMIT", 3, 1, 30)
DIST_TRAFFIC_BROWSER_LIMIT = _env_int("DIST_TRAFFIC_BROWSER_LIMIT", 1, 1, 8)
DIST_TRAFFIC_GLOBAL_LIMIT = _env_int("DIST_TRAFFIC_GLOBAL_LIMIT", 8, 2, 40)
DIST_TRAFFIC_TOKEN_SECONDS = _env_int("DIST_TRAFFIC_TOKEN_SECONDS", 90, 10, 180)
DIST_TRAFFIC_SHARED_COOLDOWN = _env_bool("DIST_TRAFFIC_SHARED_COOLDOWN", True)


class DistributedUnavailable(RuntimeError):
    pass


class DistributedCoordinator:
    """Redis coordination layer for DT PARSER.

    Telegram polling remains a single service. Parser worker replicas consume the
    Redis Stream. PostgreSQL stays the source of truth for scan state/results.
    Redis is intentionally coordination/cache only, so losing Redis never destroys
    user data already stored in PostgreSQL.
    """

    def __init__(self) -> None:
        self.url = REDIS_URL
        self.enabled = DISTRIBUTED_WORKERS
        self._redis: Any | None = None
        self._group_ready = False
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> Any:
        if not self.enabled:
            raise DistributedUnavailable("distributed mode disabled")
        if Redis is None:
            raise DistributedUnavailable("redis package is not installed")
        if self._redis is not None:
            return self._redis
        async with self._connect_lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=10,
                    health_check_interval=30,
                )
                await self._redis.ping()
        return self._redis

    async def close(self) -> None:
        redis = self._redis
        self._redis = None
        self._group_ready = False
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        redis = await self.connect()
        try:
            await redis.xgroup_create(STREAM_NAME, STREAM_GROUP, id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP is expected after the first worker starts.
            if "BUSYGROUP" not in str(exc).upper():
                raise
        self._group_ready = True

    def _job_marker(self, job_uid: str) -> str:
        return f"{REDIS_PREFIX}:job:enqueued:{job_uid}"

    def _cancel_key(self, job_uid: str) -> str:
        return f"{REDIS_PREFIX}:job:cancel:{job_uid}"

    def _job_lock_key(self, job_uid: str) -> str:
        return f"{REDIS_PREFIX}:job:lock:{job_uid}"

    def _category_result_key(self, key: str) -> str:
        return f"{REDIS_PREFIX}:category:result:{key}"

    def _category_lock_key(self, key: str) -> str:
        return f"{REDIS_PREFIX}:category:lock:{key}"

    def _category_progress_key(self, key: str) -> str:
        return f"{REDIS_PREFIX}:category:progress:{key}"

    def _worker_key(self, worker_id: str) -> str:
        return f"{REDIS_PREFIX}:worker:{worker_id}"

    async def enqueue_scan(self, job_uid: str, *, force: bool = False) -> bool:
        """Add one persistent job UID to the Redis Stream.

        A marker prevents bot restarts or DB recovery from enqueueing the same scan
        twice. Workers ACK only after finalization, so a worker crash leaves the
        stream item recoverable via XAUTOCLAIM.
        """
        await self.ensure_group()
        redis = await self.connect()
        marker = self._job_marker(job_uid)
        created_marker = False
        if not force:
            created = await redis.set(marker, "1", ex=JOB_MARKER_TTL_SECONDS, nx=True)
            if not created:
                return False
            created_marker = True
        else:
            await redis.set(marker, "1", ex=JOB_MARKER_TTL_SECONDS)
        try:
            await redis.xadd(STREAM_NAME, {"job_uid": job_uid, "queued_at": str(int(time.time()))})
        except Exception:
            if created_marker:
                await redis.delete(marker)
            raise
        return True

    async def mark_job_complete(self, job_uid: str) -> None:
        redis = await self.connect()
        await redis.delete(self._job_marker(job_uid), self._cancel_key(job_uid))

    async def request_cancel(self, job_uid: str) -> None:
        redis = await self.connect()
        await redis.set(self._cancel_key(job_uid), "1", ex=24 * 3600)

    async def is_cancel_requested(self, job_uid: str) -> bool:
        redis = await self.connect()
        return bool(await redis.exists(self._cancel_key(job_uid)))

    async def acquire_job_lock(self, job_uid: str) -> str | None:
        redis = await self.connect()
        token = uuid.uuid4().hex
        ok = await redis.set(self._job_lock_key(job_uid), token, ex=JOB_LOCK_SECONDS, nx=True)
        return token if ok else None

    async def refresh_job_lock(self, job_uid: str, token: str) -> bool:
        redis = await self.connect()
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          redis.call('expire', KEYS[1], ARGV[2])
          return 1
        end
        return 0
        """
        return bool(await redis.eval(lua, 1, self._job_lock_key(job_uid), token, JOB_LOCK_SECONDS))

    async def release_job_lock(self, job_uid: str, token: str) -> None:
        redis = await self.connect()
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          return redis.call('del', KEYS[1])
        end
        return 0
        """
        await redis.eval(lua, 1, self._job_lock_key(job_uid), token)

    async def consume_scan(self, consumer: str, block_ms: int = 5000) -> tuple[str, str] | None:
        """Return (stream_message_id, job_uid), reclaiming abandoned jobs first."""
        await self.ensure_group()
        redis = await self.connect()

        # Reclaim a message abandoned by a crashed worker before reading new work.
        try:
            claimed = await redis.xautoclaim(
                STREAM_NAME,
                STREAM_GROUP,
                consumer,
                min_idle_time=PENDING_RECLAIM_IDLE_MS,
                start_id="0-0",
                count=1,
            )
            # redis-py returns (next_id, [(id, fields), ...], deleted_ids?) depending on version.
            messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
            if messages:
                msg_id, fields = messages[0]
                job_uid = (fields or {}).get("job_uid")
                if job_uid:
                    return str(msg_id), str(job_uid)
        except Exception:
            log.debug("XAUTOCLAIM unavailable/failed", exc_info=True)

        rows = await redis.xreadgroup(
            STREAM_GROUP,
            consumer,
            {STREAM_NAME: ">"},
            count=1,
            block=block_ms,
        )
        if not rows:
            return None
        _stream, messages = rows[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        job_uid = (fields or {}).get("job_uid")
        if not job_uid:
            await redis.xack(STREAM_NAME, STREAM_GROUP, msg_id)
            return None
        return str(msg_id), str(job_uid)

    async def ack_scan(self, message_id: str) -> None:
        redis = await self.connect()
        pipe = redis.pipeline(transaction=False)
        pipe.xack(STREAM_NAME, STREAM_GROUP, message_id)
        # Completed entries are not useful history; PostgreSQL owns that history.
        # Delete them so the Redis Stream stays a small work queue, not an audit log.
        pipe.xdel(STREAM_NAME, message_id)
        await pipe.execute()

    async def queue_length(self) -> int:
        redis = await self.connect()
        return int(await redis.xlen(STREAM_NAME))

    async def heartbeat(self, worker_id: str, payload: str = "parser") -> None:
        redis = await self.connect()
        await redis.set(self._worker_key(worker_id), payload, ex=WORKER_HEARTBEAT_TTL_SECONDS)

    async def worker_count(self, prefix: str | None = None) -> int:
        redis = await self.connect()
        count = 0
        pattern = f"{REDIS_PREFIX}:worker:{prefix}-*" if prefix else f"{REDIS_PREFIX}:worker:*"
        async for _key in redis.scan_iter(match=pattern):
            count += 1
        return count

    def _traffic_kind_key(self, kind: str) -> str:
        return f"{REDIS_PREFIX}:traffic:active:{kind}"

    def _traffic_global_key(self) -> str:
        return f"{REDIS_PREFIX}:traffic:active:global"

    def _traffic_lane_id(self) -> str:
        # Each Railway replica/process owns its pacing lane. Concurrency counters
        # remain global in Redis, but request-spacing is intentionally NOT global:
        # one fast worker must never starve another worker waiting on date search.
        replica = (os.getenv("RAILWAY_REPLICA_ID") or "").strip()
        if replica:
            return replica
        return f"{socket.gethostname()}:{os.getpid()}"

    def _traffic_next_key(self, kind: str) -> str:
        return f"{REDIS_PREFIX}:traffic:next:{kind}:{self._traffic_lane_id()}"

    def _traffic_cooldown_key(self) -> str:
        if DIST_TRAFFIC_SHARED_COOLDOWN:
            return f"{REDIS_PREFIX}:traffic:cooldown_until_ms"
        # Browser-isolated workers should not all freeze because one independent
        # browser session received a temporary refusal. Keep the cooldown key local
        # to this worker process while still sharing the global concurrency tokens.
        return f"{REDIS_PREFIX}:traffic:cooldown:{socket.gethostname()}:{os.getpid()}"

    async def acquire_traffic(self, kind: str, *, interval_seconds: float = 0.0) -> str:
        """Acquire a short Redis lease shared by every parser-worker replica.

        Sorted-set leases self-heal after worker crashes. Global active-request
        caps are shared by every replica, while pacing is per replica. This prevents
        one fast worker from repeatedly winning a single shared next-request clock
        and starving other users during date search.
        """
        if kind not in {"scan", "view", "browser"}:
            raise ValueError(f"unknown traffic kind: {kind}")
        redis = await self.connect()
        limits = {
            "scan": DIST_TRAFFIC_SCAN_LIMIT,
            "view": DIST_TRAFFIC_VIEW_LIMIT,
            "browser": DIST_TRAFFIC_BROWSER_LIMIT,
        }
        per_limit = limits[kind]
        interval_ms = max(0, int(float(interval_seconds) * 1000))
        token = uuid.uuid4().hex
        lua = r"""
        local now = tonumber(ARGV[1])
        local expiry = tonumber(ARGV[2])
        local per_limit = tonumber(ARGV[3])
        local global_limit = tonumber(ARGV[4])
        local next_ms = tonumber(redis.call('get', KEYS[3]) or '0')
        local cooldown = tonumber(redis.call('get', KEYS[4]) or '0')
        if cooldown > now then
          return cooldown - now
        end
        if next_ms > now then
          return next_ms - now
        end
        redis.call('zremrangebyscore', KEYS[1], '-inf', now)
        redis.call('zremrangebyscore', KEYS[2], '-inf', now)
        if redis.call('zcard', KEYS[1]) >= per_limit then
          return 100
        end
        if redis.call('zcard', KEYS[2]) >= global_limit then
          return 100
        end
        redis.call('zadd', KEYS[1], expiry, ARGV[5])
        redis.call('zadd', KEYS[2], expiry, ARGV[5])
        redis.call('pexpire', KEYS[1], ARGV[6])
        redis.call('pexpire', KEYS[2], ARGV[6])
        if tonumber(ARGV[7]) > 0 then
          redis.call('set', KEYS[3], now + tonumber(ARGV[7]), 'PX', math.max(1000, tonumber(ARGV[7]) * 4))
        end
        return 0
        """
        ttl_ms = DIST_TRAFFIC_TOKEN_SECONDS * 1000
        while True:
            now_ms = int(time.time() * 1000)
            wait_ms = int(await redis.eval(
                lua,
                4,
                self._traffic_kind_key(kind),
                self._traffic_global_key(),
                self._traffic_next_key(kind),
                self._traffic_cooldown_key(),
                now_ms,
                now_ms + ttl_ms,
                per_limit,
                DIST_TRAFFIC_GLOBAL_LIMIT,
                token,
                ttl_ms * 2,
                interval_ms,
            ))
            if wait_ms <= 0:
                return token
            await asyncio.sleep(min(1.5, max(0.03, wait_ms / 1000.0)))

    async def release_traffic(self, kind: str, token: str) -> None:
        redis = await self.connect()
        pipe = redis.pipeline(transaction=False)
        pipe.zrem(self._traffic_kind_key(kind), token)
        pipe.zrem(self._traffic_global_key(), token)
        await pipe.execute()

    async def report_traffic_refusal(self, status_code: int) -> None:
        if int(status_code) not in {403, 429}:
            return
        redis = await self.connect()
        count_key = f"{REDIS_PREFIX}:traffic:refusals_60s"
        count = int(await redis.incr(count_key))
        if count == 1:
            await redis.expire(count_key, 60)
        cooldown_seconds = 5 if count <= 1 else 8 if count == 2 else 15 if count <= 5 else 30
        until_ms = int((time.time() + cooldown_seconds) * 1000)
        lua = r"""
        local current = tonumber(redis.call('get', KEYS[1]) or '0')
        local wanted = tonumber(ARGV[1])
        if wanted > current then
          redis.call('set', KEYS[1], wanted, 'PX', ARGV[2])
        end
        return 1
        """
        await redis.eval(lua, 1, self._traffic_cooldown_key(), until_ms, (cooldown_seconds + 5) * 1000)

    async def get_category_result(self, key: str) -> dict[str, Any] | None:
        redis = await self.connect()
        raw = await redis.get(self._category_result_key(key))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_category_result(self, key: str, result: Any, ttl_seconds: int) -> None:
        redis = await self.connect()
        payload = asdict(result) if is_dataclass(result) else result
        await redis.set(
            self._category_result_key(key),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ex=max(1, int(ttl_seconds)),
        )

    async def delete_category_result(self, key: str) -> None:
        redis = await self.connect()
        await redis.delete(self._category_result_key(key))

    async def acquire_category_lock(self, key: str) -> str | None:
        redis = await self.connect()
        token = uuid.uuid4().hex
        ok = await redis.set(
            self._category_lock_key(key), token, ex=CATEGORY_LOCK_SECONDS, nx=True
        )
        return token if ok else None

    async def category_lock_exists(self, key: str) -> bool:
        redis = await self.connect()
        return bool(await redis.exists(self._category_lock_key(key)))

    async def refresh_category_lock(self, key: str, token: str) -> bool:
        redis = await self.connect()
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          redis.call('expire', KEYS[1], ARGV[2])
          return 1
        end
        return 0
        """
        return bool(await redis.eval(lua, 1, self._category_lock_key(key), token, CATEGORY_LOCK_SECONDS))

    async def release_category_lock(self, key: str, token: str) -> None:
        redis = await self.connect()
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          return redis.call('del', KEYS[1])
        end
        return 0
        """
        await redis.eval(lua, 1, self._category_lock_key(key), token)

    async def set_category_progress(self, key: str, progress: Any) -> None:
        redis = await self.connect()
        payload = asdict(progress) if is_dataclass(progress) else progress
        await redis.set(
            self._category_progress_key(key),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ex=CATEGORY_PROGRESS_TTL_SECONDS,
        )

    async def get_category_progress(self, key: str) -> dict[str, Any] | None:
        redis = await self.connect()
        raw = await redis.get(self._category_progress_key(key))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def clear_category_progress(self, key: str) -> None:
        redis = await self.connect()
        await redis.delete(self._category_progress_key(key))


COORDINATOR = DistributedCoordinator()


def default_worker_id(prefix: str = "parser") -> str:
    host = socket.gethostname().split(".")[0]
    return f"{prefix}-{host}-{os.getpid()}"
