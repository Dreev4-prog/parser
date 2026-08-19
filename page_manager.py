from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

from parser import CategoryPageInfo, ParsedListing

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover - dependency is present in production
    Redis = None  # type: ignore

log = logging.getLogger("dtparser-page-manager")


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
# v4.3.21: page delegation is opportunistic. If Redis exists, the main bot may
# use a Page Worker when a heartbeat is present; otherwise the known-good local
# parser path is used immediately. Set REMOTE_PAGE_WORKER_ENABLED=0 for an instant
# rollback without changing code or removing the Railway service.
REMOTE_PAGE_WORKER_ENABLED = _env_bool("REMOTE_PAGE_WORKER_ENABLED", bool(REDIS_URL))
PAGE_REDIS_PREFIX = os.getenv("PAGE_REDIS_PREFIX", "dtparser:pageworker").strip() or "dtparser:pageworker"
PAGE_STREAM = f"{PAGE_REDIS_PREFIX}:jobs"
PAGE_GROUP = f"{PAGE_REDIS_PREFIX}:workers"
PAGE_HEARTBEAT_KEY = f"{PAGE_REDIS_PREFIX}:heartbeat"
PAGE_CACHE_TTL_SECONDS = _env_int("PAGE_CACHE_TTL_SECONDS", 180, 30, 900)
PAGE_PENDING_TTL_SECONDS = _env_int("PAGE_PENDING_TTL_SECONDS", 180, 30, 900)
PAGE_ERROR_TTL_SECONDS = _env_int("PAGE_ERROR_TTL_SECONDS", 45, 10, 300)
PAGE_REMOTE_TIMEOUT_SECONDS = _env_int("PAGE_REMOTE_TIMEOUT_SECONDS", 150, 20, 600)
PAGE_REMOTE_STALL_SECONDS = _env_int("PAGE_REMOTE_STALL_SECONDS", 25, 8, 120)
PAGE_HEARTBEAT_STALE_SECONDS = _env_int("PAGE_HEARTBEAT_STALE_SECONDS", 20, 8, 120)
PAGE_PROGRESS_POLL_MS = _env_int("PAGE_PROGRESS_POLL_MS", 250, 100, 2000)
# v4.3.22 streaming Page Worker: the foreground scan never waits for the whole
# 15/25/50-page batch. It may wait briefly for only the *next* page when a worker
# already owns it, which avoids duplicate local+remote fetches without a 0/N pause.
PAGE_CACHE_WAIT_MS = _env_int("PAGE_CACHE_WAIT_MS", 1800, 0, 5000)
PAGE_CACHE_WAIT_POLL_MS = _env_int("PAGE_CACHE_WAIT_POLL_MS", 100, 50, 500)
PAGE_PREFETCH_ENABLED = _env_bool("PAGE_PREFETCH_ENABLED", True)
PAGE_PREFETCH_MIN_PAGES = _env_int("PAGE_PREFETCH_MIN_PAGES", 4, 1, 50)
PAGE_PREFETCH_EXTRA_PAGES = _env_int("PAGE_PREFETCH_EXTRA_PAGES", 3, 0, 10)


def page_cache_id(url: str, requested_page: int) -> str:
    raw = f"{int(requested_page)}\n{url}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def serialize_page_info(info: CategoryPageInfo) -> str:
    payload = {
        "requested_page": int(info.requested_page),
        "final_url": str(info.final_url or ""),
        "items": [
            {
                "external_id": str(item.external_id or ""),
                "title": str(item.title or ""),
                "price_text": item.price_text,
                "price_eur": item.price_eur,
                "url": str(item.url or ""),
                "posted_text": item.posted_text,
            }
            for item in (info.items or [])
        ],
        "result_start": info.result_start,
        "result_end": info.result_end,
        "total_results": info.total_results,
        "actual_page": info.actual_page,
        "max_page": info.max_page,
        "request_matches_page": bool(info.request_matches_page),
        "page_verified": bool(info.page_verified),
        "fingerprint": str(info.fingerprint or ""),
        "raw_candidates": int(info.raw_candidates or 0),
        "promoted_filtered": int(info.promoted_filtered or 0),
        "promoted_ids": list(info.promoted_ids or []),
        "duplicate_cards": int(info.duplicate_cards or 0),
        "missing_date_count": int(info.missing_date_count or 0),
        "missing_price_count": int(info.missing_price_count or 0),
        "date_coverage": float(info.date_coverage or 0.0),
        "suspicious": bool(info.suspicious),
        "warnings": list(info.warnings or []),
        "location_shards": [list(x) for x in (info.location_shards or [])],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def deserialize_page_info(raw: str | bytes | None) -> CategoryPageInfo | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        items: list[ParsedListing] = []
        for row in data.get("items") or []:
            if not isinstance(row, dict):
                continue
            external_id = str(row.get("external_id") or "")
            url = str(row.get("url") or "")
            if not external_id or not url:
                continue
            items.append(ParsedListing(
                external_id=external_id,
                title=str(row.get("title") or ""),
                price_text=row.get("price_text"),
                price_eur=(int(row["price_eur"]) if row.get("price_eur") is not None else None),
                url=url,
                posted_text=row.get("posted_text"),
            ))
        shards: list[tuple[str, int | None]] = []
        for shard in data.get("location_shards") or []:
            if not isinstance(shard, (list, tuple)) or not shard:
                continue
            shard_url = str(shard[0] or "")
            if not shard_url:
                continue
            count = None
            if len(shard) > 1 and shard[1] is not None:
                try:
                    count = int(shard[1])
                except Exception:
                    count = None
            shards.append((shard_url, count))
        return CategoryPageInfo(
            requested_page=max(1, int(data.get("requested_page") or 1)),
            final_url=str(data.get("final_url") or ""),
            items=items,
            result_start=(int(data["result_start"]) if data.get("result_start") is not None else None),
            result_end=(int(data["result_end"]) if data.get("result_end") is not None else None),
            total_results=(int(data["total_results"]) if data.get("total_results") is not None else None),
            actual_page=(int(data["actual_page"]) if data.get("actual_page") is not None else None),
            max_page=(int(data["max_page"]) if data.get("max_page") is not None else None),
            request_matches_page=bool(data.get("request_matches_page", True)),
            page_verified=bool(data.get("page_verified", False)),
            fingerprint=str(data.get("fingerprint") or ""),
            raw_candidates=int(data.get("raw_candidates") or 0),
            promoted_filtered=int(data.get("promoted_filtered") or 0),
            promoted_ids=[str(x) for x in (data.get("promoted_ids") or []) if str(x)],
            duplicate_cards=int(data.get("duplicate_cards") or 0),
            missing_date_count=int(data.get("missing_date_count") or 0),
            missing_price_count=int(data.get("missing_price_count") or 0),
            date_coverage=float(data.get("date_coverage") or 0.0),
            suspicious=bool(data.get("suspicious", False)),
            warnings=[str(x) for x in (data.get("warnings") or [])],
            location_shards=shards,
        )
    except Exception:
        log.debug("Could not deserialize Page Worker payload", exc_info=True)
        return None


class RemotePageManager:
    """Redis page-prefetch/cache client for the stable local scan engine.

    Date location remains in the main bot. Once the first target page is known,
    this manager asks dedicated Railway Page Worker replicas to warm a short-lived
    cache of the upcoming result pages. The existing scan_one_category logic then
    consumes those exact CategoryPageInfo objects in normal chronological order.

    Any miss, worker outage, malformed result or timeout simply falls back to the
    original local parser page fetch. Redis is acceleration only, never truth.
    """

    def __init__(self) -> None:
        self.url = REDIS_URL
        self.enabled = bool(REMOTE_PAGE_WORKER_ENABLED and self.url)
        self._redis: Any | None = None
        self._lock = asyncio.Lock()
        self.cache_hits_total = 0
        self.cache_misses_total = 0
        self.prefetch_batches_total = 0
        self.prefetch_pages_total = 0
        self.prefetch_remote_total = 0
        self.prefetch_errors_total = 0
        self.last_batch_pages = 0
        self.last_batch_cached = 0
        self.last_batch_remote = 0
        self.last_batch_failed = 0
        self.last_batch_workers = 0
        self.last_batch_seconds = 0.0
        self.last_batch_at = 0.0

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

    def cache_key(self, cache_id: str) -> str:
        return f"{PAGE_REDIS_PREFIX}:cache:{cache_id}"

    def pending_key(self, cache_id: str) -> str:
        return f"{PAGE_REDIS_PREFIX}:pending:{cache_id}"

    def error_key(self, cache_id: str) -> str:
        return f"{PAGE_REDIS_PREFIX}:error:{cache_id}"

    async def worker_count(self) -> int:
        if not self.enabled:
            return 0
        try:
            redis = await self.connect()
            count = 0
            async for key in redis.scan_iter(match=f"{PAGE_REDIS_PREFIX}:worker:*", count=50):
                raw = await redis.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    ts = float(data.get("ts", 0.0))
                except Exception:
                    continue
                if (time.time() - ts) <= PAGE_HEARTBEAT_STALE_SECONDS:
                    count += 1
            if count:
                return count
            raw = await redis.get(PAGE_HEARTBEAT_KEY)
            if raw:
                try:
                    data = json.loads(raw)
                    if (time.time() - float(data.get("ts", 0.0))) <= PAGE_HEARTBEAT_STALE_SECONDS:
                        return 1
                except Exception:
                    pass
            return 0
        except Exception:
            log.debug("Page Worker count check failed", exc_info=True)
            return 0

    async def worker_alive(self) -> bool:
        return (await self.worker_count()) > 0

    async def get_cached(self, url: str, requested_page: int) -> CategoryPageInfo | None:
        if not self.enabled:
            return None
        try:
            redis = await self.connect()
            cache_id = page_cache_id(url, requested_page)
            raw = await redis.get(self.cache_key(cache_id))
            info = deserialize_page_info(raw)
            if info is not None:
                self.cache_hits_total += 1
                return info
            self.cache_misses_total += 1
        except Exception:
            log.debug("Page Worker cache read failed", exc_info=True)
        return None

    async def get_cached_wait(
        self, url: str, requested_page: int, *, wait_ms: int | None = None
    ) -> CategoryPageInfo | None:
        """Return a warmed page, briefly waiting only when a worker owns it.

        v4.3.21 waited for the entire prefetch batch before collection could start,
        which produced the visible 0/50 pause. v4.3.22 streams instead: the scan
        starts immediately and only the next required page gets a tiny bounded wait.
        If no Page Worker currently owns that page, return immediately so the stable
        local parser remains the fallback.
        """
        if not self.enabled:
            return None
        budget_ms = PAGE_CACHE_WAIT_MS if wait_ms is None else max(0, int(wait_ms))
        try:
            redis = await self.connect()
            cache_id = page_cache_id(url, requested_page)
            deadline = time.monotonic() + (budget_ms / 1000.0)
            counted_miss = False
            while True:
                pipe = redis.pipeline(transaction=False)
                pipe.get(self.cache_key(cache_id))
                pipe.exists(self.pending_key(cache_id))
                pipe.get(self.error_key(cache_id))
                raw, pending, error = await pipe.execute()
                info = deserialize_page_info(raw)
                if info is not None:
                    self.cache_hits_total += 1
                    return info
                if not counted_miss:
                    self.cache_misses_total += 1
                    counted_miss = True
                # No owner (or an explicit worker error) means local fallback now.
                if error or not pending or budget_ms <= 0 or time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(min(PAGE_CACHE_WAIT_POLL_MS / 1000.0, max(0.0, deadline - time.monotonic())))
        except Exception:
            log.debug("Page Worker cache wait failed", exc_info=True)
            return None

    async def store_cached(self, url: str, requested_page: int, info: CategoryPageInfo) -> None:
        """Publish a locally-fetched page into the same 180-second shared cache.

        This makes the fallback cooperative: if the foreground parser beats the Page
        Worker to a page, simultaneous users can reuse that result instead of fetching
        the page yet again.
        """
        if not self.enabled or info is None:
            return
        try:
            redis = await self.connect()
            cache_id = page_cache_id(url, requested_page)
            raw = serialize_page_info(info)
            pipe = redis.pipeline(transaction=False)
            pipe.set(self.cache_key(cache_id), raw, ex=PAGE_CACHE_TTL_SECONDS)
            pipe.delete(self.pending_key(cache_id), self.error_key(cache_id))
            await pipe.execute()
        except Exception:
            log.debug("Could not publish local page into Page Worker cache", exc_info=True)

    async def prefetch(
        self,
        requests: list[tuple[int, str]],
        *,
        progress_cb: Callable[[int, int], Awaitable[None] | None] | None = None,
        wait_for_results: bool = True,
    ) -> dict[int, CategoryPageInfo]:
        """Warm and return a page range through Redis Page Worker replicas.

        `requests` is [(requested_page, full_url), ...]. Page-level SET NX pending
        markers provide single-flight across simultaneous users. A second user waits
        for the same 180-second cache entry instead of making another site request.
        """
        if not self.enabled or not PAGE_PREFETCH_ENABLED or not requests:
            return {}
        dedup: dict[str, tuple[int, str, str]] = {}
        for requested_page, url in requests:
            page = max(1, int(requested_page))
            text_url = str(url or "")
            if not text_url:
                continue
            cid = page_cache_id(text_url, page)
            dedup[cid] = (page, text_url, cid)
        if len(dedup) < PAGE_PREFETCH_MIN_PAGES:
            return {}

        redis = await self.connect()
        workers = await self.worker_count()
        if workers <= 0:
            return {}

        started = time.monotonic()
        self.prefetch_batches_total += 1
        self.prefetch_pages_total += len(dedup)
        self.last_batch_pages = len(dedup)
        self.last_batch_workers = workers
        self.last_batch_at = time.time()
        self.last_batch_cached = 0
        self.last_batch_remote = 0
        self.last_batch_failed = 0

        pending: dict[str, tuple[int, str, str]] = dict(dedup)
        results: dict[int, CategoryPageInfo] = {}

        # One MGET checks the shared 180-second page cache before enqueueing work.
        ids = list(pending)
        cache_keys = [self.cache_key(cid) for cid in ids]
        raws = await redis.mget(cache_keys)
        for cid, raw in zip(ids, raws):
            info = deserialize_page_info(raw)
            if info is None:
                continue
            page, _url, _cid = pending.pop(cid)
            results[page] = info
            self.last_batch_cached += 1
            self.cache_hits_total += 1

        # Single-flight enqueue in two Redis round trips instead of one round trip
        # per page. This removes dispatch latency for 25/50-page scans and ensures
        # both replicas see work immediately.
        enqueued = 0
        owned: list[tuple[str, int, str]] = []
        if pending:
            ids_to_claim = list(pending)
            claim_pipe = redis.pipeline(transaction=False)
            tokens: dict[str, str] = {}
            for cid in ids_to_claim:
                token = f"{os.getpid()}:{time.time_ns()}:{cid[:8]}"
                tokens[cid] = token
                claim_pipe.set(self.pending_key(cid), token, ex=PAGE_PENDING_TTL_SECONDS, nx=True)
            try:
                ownership = await claim_pipe.execute()
            except Exception:
                ownership = [False] * len(ids_to_claim)
                self.prefetch_errors_total += len(ids_to_claim)
                log.debug("Could not claim Page Worker batch", exc_info=True)

            for cid, is_owner in zip(ids_to_claim, ownership):
                if not is_owner:
                    continue
                page, url, _cid = pending[cid]
                owned.append((cid, page, url))

            if owned:
                enqueue_pipe = redis.pipeline(transaction=False)
                for cid, page, url in owned:
                    enqueue_pipe.delete(self.error_key(cid))
                    enqueue_pipe.xadd(PAGE_STREAM, {
                        "cache_id": cid,
                        "url": url,
                        "page": str(page),
                        "queued_at": str(int(time.time())),
                    })
                try:
                    await enqueue_pipe.execute()
                    enqueued = len(owned)
                except Exception:
                    self.prefetch_errors_total += len(owned)
                    cleanup = redis.pipeline(transaction=False)
                    for cid, _page, _url in owned:
                        cleanup.delete(self.pending_key(cid))
                    try:
                        await cleanup.execute()
                    except Exception:
                        pass
                    log.debug("Could not enqueue Page Worker batch", exc_info=True)
        self.prefetch_remote_total += enqueued
        self.last_batch_remote = enqueued

        total = len(dedup)
        if progress_cb is not None:
            try:
                maybe = progress_cb(len(results), total)
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception:
                pass

        # Streaming mode: scheduling is complete, so hand control back to the stable
        # foreground collector immediately. Workers continue warming cache entries in
        # parallel and fetch() consumes each page as soon as it becomes available.
        if not wait_for_results:
            self.last_batch_seconds = max(0.0, time.monotonic() - started)
            return results

        deadline = time.monotonic() + PAGE_REMOTE_TIMEOUT_SECONDS
        last_progress_at = time.monotonic()
        while pending and time.monotonic() < deadline:
            ids = list(pending)
            pipe = redis.pipeline(transaction=False)
            for cid in ids:
                pipe.get(self.cache_key(cid))
                pipe.get(self.error_key(cid))
            values = await pipe.execute()
            completed_now = 0
            for idx, cid in enumerate(ids):
                raw = values[idx * 2]
                error = values[idx * 2 + 1]
                if raw:
                    info = deserialize_page_info(raw)
                    if info is not None:
                        page, _url, _cid = pending.pop(cid)
                        results[page] = info
                        completed_now += 1
                        self.cache_hits_total += 1
                        continue
                if error:
                    pending.pop(cid, None)
                    self.last_batch_failed += 1
                    self.prefetch_errors_total += 1
            if completed_now:
                last_progress_at = time.monotonic()
            if progress_cb is not None and completed_now:
                try:
                    maybe = progress_cb(len(results), total)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception:
                    pass
            if pending and (time.monotonic() - last_progress_at) >= PAGE_REMOTE_STALL_SECONDS:
                # Never let one wedged remote page make the stable foreground scan
                # wait for the full absolute timeout. Missing pages fall back local.
                break
            if pending:
                await asyncio.sleep(PAGE_PROGRESS_POLL_MS / 1000.0)

        if pending:
            self.last_batch_failed += len(pending)
            self.prefetch_errors_total += len(pending)
        self.last_batch_seconds = max(0.0, time.monotonic() - started)
        return results

    async def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "enabled": self.enabled,
            "alive": False,
            "queue_depth": 0,
            "workers": [],
            "cache_ttl": PAGE_CACHE_TTL_SECONDS,
            "prefetch_enabled": PAGE_PREFETCH_ENABLED,
            "streaming": True,
            "cache_wait_ms": PAGE_CACHE_WAIT_MS,
            "cache_hits_total": self.cache_hits_total,
            "cache_misses_total": self.cache_misses_total,
            "prefetch_batches_total": self.prefetch_batches_total,
            "prefetch_pages_total": self.prefetch_pages_total,
            "prefetch_remote_total": self.prefetch_remote_total,
            "prefetch_errors_total": self.prefetch_errors_total,
            "last_batch_pages": self.last_batch_pages,
            "last_batch_cached": self.last_batch_cached,
            "last_batch_remote": self.last_batch_remote,
            "last_batch_failed": self.last_batch_failed,
            "last_batch_workers": self.last_batch_workers,
            "last_batch_seconds": self.last_batch_seconds,
            "last_batch_at": self.last_batch_at,
            "error": None,
        }
        if not self.enabled:
            return base
        try:
            redis = await self.connect()
            try:
                base["queue_depth"] = int(await redis.xlen(PAGE_STREAM))
            except Exception:
                base["queue_depth"] = -1
            workers: list[dict[str, Any]] = []
            async for key in redis.scan_iter(match=f"{PAGE_REDIS_PREFIX}:worker:*", count=50):
                raw = await redis.get(key)
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                    age = max(0.0, time.time() - float(item.get("ts", 0.0)))
                except Exception:
                    continue
                if age <= PAGE_HEARTBEAT_STALE_SECONDS:
                    item["age_seconds"] = age
                    workers.append(item)
            base["workers"] = workers
            base["alive"] = bool(workers)
            if workers:
                base["active_total"] = sum(int(x.get("active", 0) or 0) for x in workers)
                base["processed_total"] = sum(int(x.get("processed", 0) or 0) for x in workers)
                base["cache_served_total"] = sum(int(x.get("cache_served", 0) or 0) for x in workers)
                base["errors_total"] = sum(int(x.get("errors", 0) or 0) for x in workers)
                base["rate_total"] = sum(float(x.get("rate_ema", 0.0) or 0.0) for x in workers)
            return base
        except Exception as exc:
            base["error"] = str(exc)[:300]
            return base


REMOTE_PAGE_MANAGER = RemotePageManager()
