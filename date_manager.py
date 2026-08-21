from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
DATE_PREWARM_KEY = f"{DATE_REDIS_PREFIX}:prewarm:event"
DATE_PREWARM_GATE_KEY = f"{DATE_REDIS_PREFIX}:prewarm:gate"
DATE_PREWARM_EVENT_TTL_SECONDS = _env_int("DATE_PREWARM_EVENT_TTL_SECONDS", 600, 60, 3600)
DATE_PREWARM_DEBOUNCE_SECONDS = _env_int("DATE_PREWARM_DEBOUNCE_SECONDS", 90, 15, 600)
DATE_CACHE_TTL_SECONDS = _env_int("DATE_CACHE_TTL_SECONDS", 180, 30, 900)
DATE_PENDING_TTL_SECONDS = _env_int("DATE_PENDING_TTL_SECONDS", 90, 20, 300)
DATE_ERROR_TTL_SECONDS = _env_int("DATE_ERROR_TTL_SECONDS", 45, 10, 180)
DATE_HEARTBEAT_STALE_SECONDS = _env_int("DATE_HEARTBEAT_STALE_SECONDS", 20, 8, 120)
DATE_PROBE_TIMEOUT_SECONDS = _env_int("DATE_PROBE_TIMEOUT_SECONDS", 10, 3, 45)
DATE_PROBE_POLL_MS = _env_int("DATE_PROBE_POLL_MS", 120, 50, 1000)
DATE_EXPECTED_REPLICAS = _env_int("DATE_EXPECTED_REPLICAS", 4, 1, 16)
DATE_MAX_AGE_DAYS = 4  # hard product limit: today + previous four days = five calendar dates
DATE_INITIAL_PROBES = (1, 2, 4, 8, 16, 32, 50)

# v4.3.28 Cold Date Turbo. A first-ever search has no predictor history, so
# probing the old exponential ladder wastes wall-clock time on the oldest allowed
# dates. Queue a broad age-aware grid in one Redis batch; two Date Worker replicas
# can consume the probes concurrently. These are hints only: the foreground parser
# still locally verifies the boundary before it becomes truth.
DATE_COLD_TURBO_ENABLED = _env_bool("DATE_COLD_TURBO_ENABLED", True)
DATE_COLD_TURBO_TIMEOUT_SECONDS = _env_int("DATE_COLD_TURBO_TIMEOUT_SECONDS", 8, 3, 20)
MOSCOW = ZoneInfo("Europe/Moscow")

def cold_date_probe_pages(target_date: str) -> list[int]:
    try:
        target = date.fromisoformat(str(target_date))
        today = datetime.now(MOSCOW).date()
        age = max(0, min(DATE_MAX_AGE_DAYS, (today - target).days))
    except Exception:
        age = 0
    if age <= 0:
        pages = (1, 2, 4, 7, 11, 17, 26, 38, 50)
    elif age == 1:
        pages = (1, 3, 6, 10, 15, 22, 31, 40, 50)
    elif age == 2:
        pages = (1, 4, 8, 13, 19, 26, 34, 42, 50)
    elif age == 3:
        pages = (1, 6, 11, 17, 24, 31, 38, 44, 50)
    elif age == 4:
        pages = (1, 7, 14, 21, 28, 35, 41, 46, 50)
    else:
        # Defensive fallback for any future wider date window. Spread
        # probes nearly uniformly over the public 50-page window so the first
        # round brackets the date instead of walking 1/2/4/8/16/32/50.
        # v4.3.29 SAFE Cold Turbo: seven wide probes are enough to bracket an
        # old date while keeping the first cold pass below the request burst that
        # caused transient page verification failures in v4.3.28.
        pages = (1, 9, 17, 25, 33, 41, 50)
    return list(dict.fromkeys(max(1, min(50, int(page))) for page in pages))

# v4.3.26: confirmed boundary predictor. Unlike the short 180s probe cache,
# predictor hints live longer because they are only starting points. The main bot
# still locally revalidates the final boundary before accepting a date.
DATE_PREDICTOR_TTL_SECONDS = _env_int("DATE_PREDICTOR_TTL_SECONDS", 3600, 300, 21600)
DATE_PREDICTOR_EXACT_RADIUS = _env_int("DATE_PREDICTOR_EXACT_RADIUS", 3, 1, 8)
DATE_PREDICTOR_ESTIMATE_RADIUS = _env_int("DATE_PREDICTOR_ESTIMATE_RADIUS", 6, 2, 12)
# v4.3.27: when a learned boundary drifts outside the first predictor window,
# keep expanding from that learned position instead of throwing the work away
# and restarting at page 1.  These are acceleration limits only; the foreground
# stable parser still owns final date verification.
DATE_PREDICTOR_CONTINUE_MAX_ROUNDS = _env_int("DATE_PREDICTOR_CONTINUE_MAX_ROUNDS", 4, 1, 6)
DATE_PREDICTOR_CONTINUE_TIMEOUT_SECONDS = _env_int("DATE_PREDICTOR_CONTINUE_TIMEOUT_SECONDS", 5, 3, 12)


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
        self.predictor_hits_total = 0
        self.predictor_misses_total = 0
        self.predictor_writes_total = 0
        self.last_predictor_page = 0
        self.last_predictor_source = ""
        self.last_predictor_points = 0
        self.predictor_continue_total = 0
        self.predictor_continue_success_total = 0
        self.last_predictor_continue_rounds = 0
        self.last_predictor_continue_pages = 0
        self.last_predictor_fallback = False
        self.cold_turbo_total = 0
        self.cold_turbo_public_beyond_total = 0
        self.last_cold_turbo = False
        self.last_cold_age_days = 0
        self.last_cold_probe_pages: list[int] = []
        self.prewarm_requests_total = 0
        self.last_prewarm_at = 0.0

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

    @staticmethod
    def _predictor_namespace(base_url: str) -> str:
        normalized = private_provider_url(base_url).strip()
        return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:24]

    def predictor_key(self, base_url: str, target_date: str) -> str:
        return f"{DATE_REDIS_PREFIX}:predict:{self._predictor_namespace(base_url)}:{target_date}"

    @staticmethod
    def _decode_predictor(raw: str | bytes | None) -> dict[str, Any] | None:
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            item = json.loads(raw)
            page = int(item.get("page") or 0)
            target = str(item.get("target_date") or "")
            if page < 1 or page > 50 or not target:
                return None
            return {
                "page": page,
                "target_date": target,
                "confirmed_at": float(item.get("confirmed_at") or 0.0),
            }
        except Exception:
            return None

    async def record_confirmed_hint(self, base_url: str, target_date: str, page: int) -> None:
        """Remember a locally confirmed boundary as a future starting point.

        Only the foreground stable parser calls this method after exact local
        verification. Date Worker guesses are never promoted into predictor data.
        """
        if not self.enabled:
            return
        try:
            page = max(1, min(50, int(page)))
            # Validate date shape before it becomes part of an interpolation set.
            date.fromisoformat(str(target_date))
            redis = await self.connect()
            raw = json.dumps({
                "page": page,
                "target_date": str(target_date),
                "confirmed_at": time.time(),
            }, ensure_ascii=False, separators=(",", ":"))
            await redis.set(
                self.predictor_key(base_url, str(target_date)),
                raw,
                ex=DATE_PREDICTOR_TTL_SECONDS,
            )
            self.predictor_writes_total += 1
        except Exception:
            log.debug("Date predictor write failed", exc_info=True)

    @staticmethod
    def _estimate_from_confirmed_points(
        target: date, points: list[tuple[date, int]],
    ) -> tuple[int, str] | None:
        exact = [page for day, page in points if day == target]
        if exact:
            return max(1, min(50, int(exact[-1]))), "exact"

        # A single neighbouring day does not reveal category density. Using it
        # could add work instead of removing it, so interpolation starts only
        # after this category has at least two confirmed calendar boundaries.
        unique: dict[date, int] = {}
        for day, page in points:
            unique[day] = page
        ordered = sorted(unique.items())
        if len(ordered) < 2:
            return None

        nearest = sorted(ordered, key=lambda item: abs((item[0] - target).days))[:2]
        (d1, p1), (d2, p2) = sorted(nearest)
        day_span = (d2 - d1).days
        if day_span == 0:
            return None
        slope = (float(p2) - float(p1)) / float(day_span)
        # Newer calendar dates should be closer to page 1. Reject contradictory
        # history rather than trying to be clever with noisy points.
        if slope >= -0.25 or slope < -50.0:
            return None
        predicted = round(float(p1) + slope * float((target - d1).days))
        predicted = max(1, min(50, int(predicted)))
        inside = min(d1, d2) <= target <= max(d1, d2)
        return predicted, "interpolated" if inside else "extrapolated"

    async def predictor_hint(self, base_url: str, target_date: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            target = date.fromisoformat(str(target_date))
            redis = await self.connect()
            # Exact target plus six days on either side is enough for the product's
            # bounded five-day selection window and avoids Redis key scans.
            days = [target + timedelta(days=offset) for offset in range(-6, 7)]
            keys = [self.predictor_key(base_url, day.isoformat()) for day in days]
            raws = await redis.mget(keys)
            points: list[tuple[date, int]] = []
            for day, raw in zip(days, raws):
                item = self._decode_predictor(raw)
                if item is not None:
                    points.append((day, int(item["page"])))
            estimate = self._estimate_from_confirmed_points(target, points)
            self.last_predictor_points = len(points)
            if estimate is None:
                self.predictor_misses_total += 1
                self.last_predictor_page = 0
                self.last_predictor_source = ""
                return None
            page, source = estimate
            self.predictor_hits_total += 1
            self.last_predictor_page = int(page)
            self.last_predictor_source = source
            return {"page": int(page), "source": source, "points": len(points)}
        except Exception:
            self.predictor_misses_total += 1
            log.debug("Date predictor read failed", exc_info=True)
            return None

    async def request_prewarm(self) -> bool:
        """Broadcast a cheap readiness pulse to Date Worker replicas.

        Date is HTTP-first and its httpx clients already exist at worker startup,
        so this intentionally performs no Kleinanzeigen request and never launches
        Chromium. Workers acknowledge the token through their heartbeat.
        """
        if not self.enabled:
            return False
        try:
            redis = await self.connect()
            gate = await redis.set(
                DATE_PREWARM_GATE_KEY, str(time.time()), ex=DATE_PREWARM_DEBOUNCE_SECONDS, nx=True
            )
            if not gate:
                return False
            token = f"{int(time.time())}:{os.getpid()}:{time.time_ns()}"
            await redis.set(DATE_PREWARM_KEY, token, ex=DATE_PREWARM_EVENT_TTL_SECONDS)
            self.prewarm_requests_total += 1
            self.last_prewarm_at = time.time()
            log.info("Date fleet readiness prewarm requested token=%s", token[:32])
            return True
        except Exception:
            log.debug("Could not request Date Worker prewarm", exc_info=True)
            return False

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

    @staticmethod
    def _predictor_continue_direction(results: dict[int, DateProbeResult]) -> str:
        """Choose the useful side of a stale predictor without trusting it as truth.

        Page order is newest -> oldest.  If every reliable probe is newer than the
        requested date, the boundary can only be deeper (right).  If every probe is
        already older/mixed/empty, it can only be shallower (left).  Contradictory
        evidence expands both ways and lets the normal chronology bracket decide.
        """
        if not results:
            return "both"
        if any(item.relation == "target" for item in results.values()):
            return "done"
        newer = [page for page, item in results.items() if item.relation == "newer"]
        non_newer = [
            page for page, item in results.items()
            if item.relation in {"older", "mixed", "empty"}
        ]
        if newer and not non_newer:
            return "right"
        if non_newer and not newer:
            return "left"
        return "both"

    async def _continue_from_predictor(
        self,
        base_url: str,
        target_date: str,
        *,
        center: int,
        radius: int,
        merged: dict[int, DateProbeResult],
    ) -> tuple[tuple[int, int] | None, int, int, int]:
        """Expand outward from a learned page until chronology brackets the date.

        This is the v4.3.27 fast path.  It never creates a final verdict: returned
        pages are still only remote hints and the foreground parser revalidates
        them locally before accepting a date.
        """
        total_cached = 0
        total_queued = 0
        rounds = 0
        distance = max(1, int(radius))

        for _ in range(DATE_PREDICTOR_CONTINUE_MAX_ROUNDS):
            bracket = self._boundary_from(merged)
            if bracket is not None:
                break

            rounds += 1
            distance = min(50, max(distance + 1, distance * 2))
            direction = self._predictor_continue_direction(merged)
            candidates: list[int] = []
            if direction in {"left", "both"}:
                candidates.append(max(1, center - distance))
            if direction in {"right", "both"}:
                candidates.append(min(50, center + distance))
            candidates = [p for p in sorted(set(candidates)) if p not in merged]
            if not candidates:
                # We reached page 1/50 on the useful side.  There is no more
                # remote search space to expand without repeating old requests.
                break

            extra = await self._probe_set(
                base_url,
                target_date,
                candidates,
                stop_on_boundary=True,
                timeout_seconds=float(DATE_PREDICTOR_CONTINUE_TIMEOUT_SECONDS),
            )
            total_cached += self.last_batch_cached
            total_queued += self.last_batch_queued
            merged.update(extra)

        bracket = self._boundary_from(merged)
        return bracket, total_cached, total_queued, rounds

    async def locate_hint(self, base_url: str, target_date: str) -> dict[str, Any] | None:
        """Return a remote boundary hint, never a final date verdict.

        v4.3.27 first probes around a learned boundary.  If that boundary drifted,
        the search expands outward from the learned page instead of restarting the
        old 1/2/4/8/... ladder.  The exponential locator is now only an emergency
        fallback for a cold category or unusable predictor evidence.  The main
        stable parser still verifies the returned boundary locally.
        """
        if not self.enabled:
            return None
        try:
            locate_started = time.monotonic()
            predictor = await self.predictor_hint(base_url, target_date)
            merged: dict[int, DateProbeResult] = {}
            total_cached = 0
            total_queued = 0
            bracket: tuple[int, int] | None = None
            continue_rounds = 0
            continue_pages = 0
            used_cold_fallback = False
            self.last_predictor_continue_rounds = 0
            self.last_predictor_continue_pages = 0
            self.last_predictor_fallback = False

            if predictor is not None:
                center = max(1, min(50, int(predictor["page"])))
                radius = (
                    DATE_PREDICTOR_EXACT_RADIUS
                    if predictor.get("source") == "exact"
                    else DATE_PREDICTOR_ESTIMATE_RADIUS
                )
                # First, check a tight window around the last confirmed/estimated
                # location.  This is still the common 2-5 request fast path.
                predicted_pages = sorted(set(
                    max(1, min(50, center + delta))
                    for delta in (-radius, -(max(1, radius // 2)), 0, max(1, radius // 2), radius)
                ))
                predicted = await self._probe_set(
                    base_url, target_date, predicted_pages, stop_on_boundary=True,
                    timeout_seconds=max(3.0, DATE_PROBE_TIMEOUT_SECONDS * 0.70),
                )
                total_cached += self.last_batch_cached
                total_queued += self.last_batch_queued
                merged.update(predicted)
                bracket = self._boundary_from(merged)

                # v4.3.27: do NOT discard the predictor work when the boundary
                # shifted by more than the first radius.  Expand 2x from the hint
                # and, where chronology is clear, only in the useful direction.
                if bracket is None and merged:
                    self.predictor_continue_total += 1
                    before = set(merged)
                    bracket, c_cached, c_queued, continue_rounds = await self._continue_from_predictor(
                        base_url,
                        target_date,
                        center=center,
                        radius=radius,
                        merged=merged,
                    )
                    total_cached += c_cached
                    total_queued += c_queued
                    continue_pages = len(set(merged) - before)
                    self.last_predictor_continue_rounds = continue_rounds
                    self.last_predictor_continue_pages = continue_pages
                    if bracket is not None:
                        self.predictor_continue_success_total += 1

            # v4.3.28: a truly cold category gets one broad, age-aware batch.
            # This changes wall-clock shape from a ladder of dependent rounds into
            # one parallel fan-out. Predictor failures keep the proven emergency
            # ladder because they already carry useful local evidence.
            self.last_cold_turbo = False
            self.last_cold_probe_pages = []
            if bracket is None:
                used_cold_fallback = True
                self.last_predictor_fallback = bool(predictor is not None)
                if predictor is None and DATE_COLD_TURBO_ENABLED:
                    try:
                        target = date.fromisoformat(str(target_date))
                        self.last_cold_age_days = max(0, min(DATE_MAX_AGE_DAYS, (datetime.now(MOSCOW).date() - target).days))
                    except Exception:
                        self.last_cold_age_days = 0
                    fallback_pages = [page for page in cold_date_probe_pages(target_date) if page not in merged]
                    self.last_cold_turbo = True
                    self.last_cold_probe_pages = list(fallback_pages)
                    self.cold_turbo_total += 1
                    cold_timeout = float(DATE_COLD_TURBO_TIMEOUT_SECONDS)
                else:
                    fallback_pages = [page for page in DATE_INITIAL_PROBES if page not in merged]
                    cold_timeout = None
                if fallback_pages:
                    first = await self._probe_set(
                        base_url,
                        target_date,
                        fallback_pages,
                        stop_on_boundary=True,
                        timeout_seconds=cold_timeout,
                    )
                    total_cached += self.last_batch_cached
                    total_queued += self.last_batch_queued
                    merged.update(first)
                bracket = self._boundary_from(merged)

            # A reliable page-50 `newer` result proves the requested date is beyond
            # the nationwide public window. Returning this as a special hint lets
            # the foreground parser verify page 50 once and jump straight to the
            # regional sharder instead of repeating 1/2/4/8/16/32/50 locally.
            if bracket is None:
                page50 = merged.get(50)
                if page50 is not None and page50.relation == "newer":
                    self.cold_turbo_public_beyond_total += 1
                    self.last_boundary = 50
                    self.last_batch_probes = len(merged)
                    self.last_batch_cached = total_cached
                    self.last_batch_queued = total_queued
                    self.last_batch_seconds = max(0.0, time.monotonic() - locate_started)
                    return {
                        "boundary": 50,
                        "low_newer": 50,
                        "high_non_newer": 0,
                        "direct_target": False,
                        "beyond_public": True,
                        "workers": int(self.last_batch_workers),
                        "predictor_page": int((predictor or {}).get("page") or 0),
                        "predictor_source": str((predictor or {}).get("source") or ""),
                        "predictor_points": int((predictor or {}).get("points") or 0),
                        "predictor_continue_rounds": int(continue_rounds),
                        "predictor_continue_pages": int(continue_pages),
                        "cold_fallback": bool(used_cold_fallback),
                        "cold_turbo": bool(self.last_cold_turbo),
                        "probes": {page: item.as_dict() for page, item in sorted(merged.items())},
                    }
                return None

            low, high = bracket
            # If any learned/continued probe landed directly on the target date,
            # foreground verification can start there immediately.  Otherwise use
            # a tiny quartile refinement for a wide newer/non-newer bracket.
            has_direct_target = any(item.relation == "target" for item in merged.values())
            # v4.3.37 DATE BOUNDARY RACE FIX.
            # With several Date Worker replicas, a deep target page (for example
            # page 50) can finish before the earlier probes.  A target proves that
            # the requested day exists, but it does NOT prove that this is the
            # first page of that day.  Older builds stopped refining as soon as any
            # target arrived, which could make the foreground verifier walk back
            # 40+ pages one-by-one.  Always tighten a wide bracket, even when a
            # direct target is already present.  Remote results are still hints;
            # the foreground stable parser remains the final source of truth.
            for _refine_round in range(2):
                if high - low <= 3:
                    break
                width = high - low
                refinement = sorted(set(
                    max(low + 1, min(high - 1, low + round(width * ratio)))
                    for ratio in (0.25, 0.5, 0.75)
                    if high - low > 1
                ))
                refinement = [page for page in refinement if page not in merged and low < page < high]
                if not refinement:
                    break
                extra = await self._probe_set(
                    base_url,
                    target_date,
                    refinement,
                    stop_on_boundary=False,
                    timeout_seconds=max(3.0, DATE_PROBE_TIMEOUT_SECONDS * 0.60),
                )
                total_cached += self.last_batch_cached
                total_queued += self.last_batch_queued
                merged.update(extra)
                refined = self._boundary_from(merged)
                if refined is None:
                    break
                low, high = refined
                has_direct_target = any(item.relation == "target" for item in merged.values())

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
                "predictor_page": int((predictor or {}).get("page") or 0),
                "predictor_source": str((predictor or {}).get("source") or ""),
                "predictor_points": int((predictor or {}).get("points") or 0),
                "predictor_continue_rounds": int(continue_rounds),
                "predictor_continue_pages": int(continue_pages),
                "cold_fallback": bool(used_cold_fallback),
                "cold_turbo": bool(self.last_cold_turbo),
                "beyond_public": False,
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
            "expected_replicas": DATE_EXPECTED_REPLICAS,
            "predictor_ttl": DATE_PREDICTOR_TTL_SECONDS,
            "predictor_hits_total": self.predictor_hits_total,
            "predictor_misses_total": self.predictor_misses_total,
            "predictor_writes_total": self.predictor_writes_total,
            "predictor_continue_total": self.predictor_continue_total,
            "predictor_continue_success_total": self.predictor_continue_success_total,
            "last_predictor_continue_rounds": self.last_predictor_continue_rounds,
            "last_predictor_continue_pages": self.last_predictor_continue_pages,
            "last_predictor_fallback": self.last_predictor_fallback,
            "cold_turbo_enabled": DATE_COLD_TURBO_ENABLED,
            "cold_turbo_total": self.cold_turbo_total,
            "cold_turbo_public_beyond_total": self.cold_turbo_public_beyond_total,
            "last_cold_turbo": self.last_cold_turbo,
            "last_cold_age_days": self.last_cold_age_days,
            "last_cold_probe_pages": list(self.last_cold_probe_pages),
            "last_predictor_page": self.last_predictor_page,
            "last_predictor_source": self.last_predictor_source,
            "last_predictor_points": self.last_predictor_points,
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
            "prewarm_requests_total": self.prewarm_requests_total,
            "last_prewarm_at": self.last_prewarm_at,
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
                event_at = float(self.last_prewarm_at or 0.0)
                base["prewarm_ready_total"] = (
                    sum(1 for x in workers if float(x.get("last_prewarm_at", 0.0) or 0.0) >= event_at - 2.0)
                    if event_at > 0 else 0
                )
            return base
        except Exception as exc:
            base["error"] = str(exc)[:300]
            return base


REMOTE_DATE_MANAGER = RemoteDateManager()
