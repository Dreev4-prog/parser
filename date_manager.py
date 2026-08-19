from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from parser import page_url, private_provider_url

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

log = logging.getLogger("dtparser-date-manager")


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
REMOTE_DATE_WORKER_ENABLED = _env_bool("REMOTE_DATE_WORKER_ENABLED", bool(REDIS_URL))
DATE_REDIS_PREFIX = os.getenv("DATE_REDIS_PREFIX", "dtparser:dateworker").strip() or "dtparser:dateworker"
DATE_STREAM = f"{DATE_REDIS_PREFIX}:jobs"
DATE_GROUP = f"{DATE_REDIS_PREFIX}:workers"
DATE_HEARTBEAT_KEY = f"{DATE_REDIS_PREFIX}:heartbeat"
DATE_CACHE_TTL_SECONDS = _env_int("DATE_CACHE_TTL_SECONDS", 180, 30, 900)
DATE_PENDING_TTL_SECONDS = _env_int("DATE_PENDING_TTL_SECONDS", 90, 20, 300)
DATE_ERROR_TTL_SECONDS = _env_int("DATE_ERROR_TTL_SECONDS", 45, 10, 180)
DATE_HEARTBEAT_STALE_SECONDS = _env_int("DATE_HEARTBEAT_STALE_SECONDS", 20, 8, 120)
DATE_PROBE_TIMEOUT_SECONDS = _env_int("DATE_PROBE_TIMEOUT_SECONDS", 10, 3, 45)
DATE_PROBE_POLL_MS = _env_int("DATE_PROBE_POLL_MS", 120, 50, 1000)
DATE_MAX_AGE_DAYS = 6  # hard product limit: today + previous six days = seven calendar dates
DATE_INITIAL_PROBES = (1, 2, 4, 8, 16, 32, 50)


@dataclass(slots=True)
class DateProbeResult:
    page: int
    relation: str
    max_page: int | None = None
    actual_page: int | None = None
    date_coverage: float = 0.0
    target_count: int = 0
    newer_count: int = 0
    older_count: int = 0
    newest_day: str = ""
    oldest_day: str = ""
    source: str = "remote"

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "relation": self.relation,
            "max_page": self.max_page,
            "actual_page": self.actual_page,
            "date_coverage": self.date_coverage,
            "target_count": self.target_count,
            "newer_count": self.newer_count,
            "older_count": self.older_count,
            "newest_day": self.newest_day,
            "oldest_day": self.oldest_day,
            "source": self.source,
        }


def date_probe_id(target_date: str, url: str, requested_page: int) -> str:
    raw = f"{target_date}\n{int(requested_page)}\n{url}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def _deserialize_probe(raw: str | bytes | None) -> DateProbeResult | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        relation = str(data.get("relation") or "")
        if relation not in {"target", "newer", "older", "mixed", "empty"}:
            return None
        return DateProbeResult(
            page=max(1, int(data.get("page") or 1)),
            relation=relation,
            max_page=(int(data["max_page"]) if data.get("max_page") is not None else None),
            actual_page=(int(data["actual_page"]) if data.get("actual_page") is not None else None),
            date_coverage=float(data.get("date_coverage") or 0.0),
            target_count=int(data.get("target_count") or 0),
            newer_count=int(data.get("newer_count") or 0),
            older_count=int(data.get("older_count") or 0),
            newest_day=str(data.get("newest_day") or ""),
            oldest_day=str(data.get("oldest_day") or ""),
            source=str(data.get("source") or "remote"),
        )
    except Exception:
        return None


class RemoteDateManager:
    """Acceleration-only date boundary locator backed by Redis Date Workers.

    Workers probe category pages in parallel and return chronology *hints*. The
    foreground stable parser still revalidates the final boundary locally before
    a date is accepted. Redis never becomes the source of truth.
    """

    def __init__(self) -> None:
        self.url = REDIS_URL
        self.enabled = bool(REMOTE_DATE_WORKER_ENABLED and self.url)
        self._redis: Any | None = None
        self._lock = asyncio.Lock()
        self.probe_batches_total = 0
        self.probes_queued_total = 0
        self.cache_hits_total = 0
        self.cache_misses_total = 0
        self.errors_total = 0
        self.last_batch_at = 0.0
        self.last_batch_seconds = 0.0
        self.last_batch_probes = 0
        self.last_batch_cached = 0
        self.last_batch_queued = 0
        self.last_batch_workers = 0
        self.last_boundary = 0
        self.last_target_date = ""

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

    def cache_key(self, cache_id: str) -> str:
        return f"{DATE_REDIS_PREFIX}:cache:{cache_id}"

    def pending_key(self, cache_id: str) -> str:
        return f"{DATE_REDIS_PREFIX}:pending:{cache_id}"

    def error_key(self, cache_id: str) -> str:
        return f"{DATE_REDIS_PREFIX}:error:{cache_id}"

    async def worker_count(self) -> int:
        if not self.enabled:
            return 0
        try:
            redis = await self.connect()
            now = time.time()
            count = 0
            async for key in redis.scan_iter(match=f"{DATE_REDIS_PREFIX}:worker:*"):
                raw = await redis.get(key)
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                    if now - float(item.get("ts", 0.0)) <= DATE_HEARTBEAT_STALE_SECONDS:
                        count += 1
                except Exception:
                    continue
            return count
        except Exception:
            return 0

    @staticmethod
    def _has_boundary(results: dict[int, DateProbeResult]) -> bool:
        if not results:
            return False
        if any(item.relation == "target" for item in results.values()):
            return True
        page1 = results.get(1)
        if page1 and page1.relation in {"older", "mixed", "empty"}:
            return True
        newer_pages = [page for page, item in results.items() if item.relation == "newer"]
        if not newer_pages:
            return False
        low = max(newer_pages)
        return any(
            page > low and item.relation in {"target", "older", "mixed", "empty"}
            for page, item in results.items()
        )

    @staticmethod
    def _boundary_from(results: dict[int, DateProbeResult]) -> tuple[int, int] | None:
        if not results:
            return None
        targets = sorted(page for page, item in results.items() if item.relation == "target")
        if targets:
            target = targets[0]
            low = max([page for page, item in results.items() if item.relation == "newer" and page < target] or [0])
            return low, target
        page1 = results.get(1)
        if page1 and page1.relation in {"older", "mixed", "empty"}:
            return 0, 1
        newer_pages = sorted(page for page, item in results.items() if item.relation == "newer")
        if not newer_pages:
            return None
        low = max(newer_pages)
        highs = sorted(
            page for page, item in results.items()
            if page > low and item.relation in {"target", "older", "mixed", "empty"}
        )
        if not highs:
            return None
        return low, highs[0]

    async def _probe_set(
        self,
        base_url: str,
        target_date: str,
        pages: list[int],
        *,
        stop_on_boundary: bool = True,
        timeout_seconds: float | None = None,
    ) -> dict[int, DateProbeResult]:
        if not self.enabled or not pages:
            return {}
        workers = await self.worker_count()
        if workers <= 0:
            return {}
        redis = await self.connect()
        base_url = private_provider_url(base_url)
        pages = sorted(set(max(1, min(50, int(page))) for page in pages))
        records: dict[str, tuple[int, str]] = {}
        for page in pages:
            url = page_url(base_url, page)
            cid = date_probe_id(target_date, url, page)
            records[cid] = (page, url)

        self.probe_batches_total += 1
        self.last_batch_at = time.time()
        self.last_batch_workers = workers
        self.last_batch_probes = len(records)
        self.last_batch_cached = 0
        self.last_batch_queued = 0
        self.last_target_date = target_date
        started = time.monotonic()
        results: dict[int, DateProbeResult] = {}

        ids = list(records)
        raws = await redis.mget([self.cache_key(cid) for cid in ids])
        pending_ids: list[str] = []
        for cid, raw in zip(ids, raws):
            item = _deserialize_probe(raw)
            if item is not None:
                results[item.page] = item
                self.cache_hits_total += 1
                self.last_batch_cached += 1
            else:
                self.cache_misses_total += 1
                pending_ids.append(cid)

        if stop_on_boundary and self._has_boundary(results):
            self.last_batch_seconds = max(0.0, time.monotonic() - started)
            return results

        if pending_ids:
            claim_pipe = redis.pipeline(transaction=False)
            for cid in pending_ids:
                claim_pipe.set(
                    self.pending_key(cid),
                    f"{os.getpid()}:{time.time_ns()}:{cid[:8]}",
                    ex=DATE_PENDING_TTL_SECONDS,
                    nx=True,
                )
            try:
                owners = await claim_pipe.execute()
            except Exception:
                owners = [False] * len(pending_ids)
                self.errors_total += len(pending_ids)

            owned: list[str] = [cid for cid, owner in zip(pending_ids, owners) if owner]
            if owned:
                pipe = redis.pipeline(transaction=False)
                for cid in owned:
                    page, url = records[cid]
                    pipe.delete(self.error_key(cid))
                    pipe.xadd(DATE_STREAM, {
                        "cache_id": cid,
                        "url": url,
                        "page": str(page),
                        "target_date": target_date,
                        "queued_at": str(time.time()),
                    })
                try:
                    await pipe.execute()
                    self.probes_queued_total += len(owned)
                    self.last_batch_queued += len(owned)
                except Exception:
                    self.errors_total += len(owned)
                    cleanup = redis.pipeline(transaction=False)
                    for cid in owned:
                        cleanup.delete(self.pending_key(cid))
                    try:
                        await cleanup.execute()
                    except Exception:
                        pass

        deadline = time.monotonic() + float(timeout_seconds or DATE_PROBE_TIMEOUT_SECONDS)
        unresolved = {cid for cid in records if records[cid][0] not in results}
        while unresolved and time.monotonic() < deadline:
            ids_now = list(unresolved)
            pipe = redis.pipeline(transaction=False)
            for cid in ids_now:
                pipe.get(self.cache_key(cid))
                pipe.get(self.error_key(cid))
            values = await pipe.execute()
            for index, cid in enumerate(ids_now):
                raw = values[index * 2]
                error = values[index * 2 + 1]
                item = _deserialize_probe(raw)
                if item is not None:
                    results[item.page] = item
                    unresolved.discard(cid)
                    continue
                if error:
                    unresolved.discard(cid)
                    self.errors_total += 1
            if stop_on_boundary and self._has_boundary(results):
                break
            await asyncio.sleep(DATE_PROBE_POLL_MS / 1000.0)

        self.last_batch_seconds = max(0.0, time.monotonic() - started)
        return results

    async def locate_hint(self, base_url: str, target_date: str) -> dict[str, Any] | None:
        """Return a remote boundary hint, never a final date verdict.

        Seven exponential probes are queued in parallel. Once a chronology bracket
        is visible, one optional quartile refinement round narrows the window. The
        main stable parser must still verify the returned boundary locally.
        """
        if not self.enabled:
            return None
        try:
            locate_started = time.monotonic()
            first = await self._probe_set(
                base_url,
                target_date,
                list(DATE_INITIAL_PROBES),
                stop_on_boundary=True,
            )
            total_cached = self.last_batch_cached
            total_queued = self.last_batch_queued
            bracket = self._boundary_from(first)
            if bracket is None:
                return None
            low, high = bracket
            merged = dict(first)
            if high - low > 6:
                width = high - low
                refinement = sorted(set(
                    max(low + 1, min(high - 1, low + round(width * ratio)))
                    for ratio in (0.25, 0.5, 0.75)
                    if high - low > 1
                ))
                refinement = [page for page in refinement if page not in merged and low < page < high]
                if refinement:
                    extra = await self._probe_set(
                        base_url,
                        target_date,
                        refinement,
                        stop_on_boundary=False,
                        timeout_seconds=max(3.0, DATE_PROBE_TIMEOUT_SECONDS * 0.65),
                    )
                    total_cached += self.last_batch_cached
                    total_queued += self.last_batch_queued
                    merged.update(extra)
                    refined = self._boundary_from(merged)
                    if refined is not None:
                        low, high = refined

            direct_targets = sorted(page for page, item in merged.items() if item.relation == "target")
            boundary = direct_targets[0] if direct_targets else high
            self.last_boundary = int(boundary)
            self.last_batch_probes = len(merged)
            self.last_batch_cached = total_cached
            self.last_batch_queued = total_queued
            self.last_batch_seconds = max(0.0, time.monotonic() - locate_started)
            return {
                "boundary": int(boundary),
                "low_newer": int(low),
                "high_non_newer": int(high),
                "direct_target": bool(direct_targets),
                "workers": int(self.last_batch_workers),
                "probes": {page: item.as_dict() for page, item in sorted(merged.items())},
            }
        except Exception:
            log.debug("Date Worker hint failed; local stable locator remains active", exc_info=True)
            self.errors_total += 1
            return None

    async def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "enabled": self.enabled,
            "alive": False,
            "workers": [],
            "queue_depth": 0,
            "cache_ttl": DATE_CACHE_TTL_SECONDS,
            "max_age_days": DATE_MAX_AGE_DAYS,
            "probe_batches_total": self.probe_batches_total,
            "probes_queued_total": self.probes_queued_total,
            "cache_hits_total": self.cache_hits_total,
            "cache_misses_total": self.cache_misses_total,
            "errors_total": self.errors_total,
            "last_batch_seconds": self.last_batch_seconds,
            "last_batch_probes": self.last_batch_probes,
            "last_batch_cached": self.last_batch_cached,
            "last_batch_queued": self.last_batch_queued,
            "last_batch_workers": self.last_batch_workers,
            "last_boundary": self.last_boundary,
            "last_target_date": self.last_target_date,
        }
        if not self.enabled:
            return base
        try:
            redis = await self.connect()
            base["queue_depth"] = int(await redis.xlen(DATE_STREAM))
            now = time.time()
            workers: list[dict[str, Any]] = []
            async for key in redis.scan_iter(match=f"{DATE_REDIS_PREFIX}:worker:*"):
                raw = await redis.get(key)
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                    if now - float(item.get("ts", 0.0)) <= DATE_HEARTBEAT_STALE_SECONDS:
                        workers.append(item)
                except Exception:
                    continue
            workers.sort(key=lambda item: str(item.get("consumer") or ""))
            base["workers"] = workers
            base["alive"] = bool(workers)
            if workers:
                base["active_total"] = sum(int(x.get("active", 0) or 0) for x in workers)
                base["processed_total"] = sum(int(x.get("processed", 0) or 0) for x in workers)
                base["errors_worker_total"] = sum(int(x.get("errors", 0) or 0) for x in workers)
                base["rate_total"] = sum(float(x.get("rate_ema", 0.0) or 0.0) for x in workers)
            return base
        except Exception as exc:
            base["error"] = str(exc)[:300]
            return base


REMOTE_DATE_MANAGER = RemoteDateManager()
