from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime

# Date Worker returns chronology hints only; the main bot still locally revalidates
# the final boundary. v4.3.25 makes the remote probe path HTTP-first, but keeps a
# wider HTTP concurrency while preserving the strict browser confirmation gate.
# Explicit 403/429 refusals are never bypassed through another transport.
DATE_WORKER_HTTP_FIRST = os.getenv("DATE_WORKER_HTTP_FIRST", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
_ALLOW_LEGACY_TUNING = os.getenv("DT_ALLOW_LEGACY_WORKER_TUNING", "0").strip().lower() in {
    "1", "true", "yes", "on",
}
os.environ["SCAN_TRANSPORT"] = "hybrid" if DATE_WORKER_HTTP_FIRST else "browser"
os.environ.setdefault("HYBRID_HTTP_FIRST", "1")
os.environ.setdefault("HYBRID_WATCHDOG_SECONDS", "8")
os.environ.setdefault("HYBRID_DIRECT_HTTP_RETRIES", "1")
os.environ.setdefault("HYBRID_BROWSER_FALLBACK_LIMIT", "4")
os.environ.setdefault("SHARED_BROWSER_RUNTIME", "1")

# v4.4.0 production fleet profile. Date replicas now participate in the Redis
# traffic governor instead of multiplying process-local limits. Old Railway
# tuning variables are ignored by default so a forgotten v4.3 experiment cannot
# silently make four replicas aggressive again.
if not _ALLOW_LEGACY_TUNING:
    os.environ["DATE_WORKER_CONCURRENCY"] = "2"
    os.environ["DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY"] = "1"
    os.environ["TRAFFIC_SCAN_CONCURRENCY"] = "2"
    os.environ["TRAFFIC_GLOBAL_CONCURRENCY"] = "3"
    os.environ["TRAFFIC_SCAN_MIN_INTERVAL_SECONDS"] = "0.20"
else:
    os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "2")
    os.environ.setdefault("TRAFFIC_GLOBAL_CONCURRENCY", "3")
    os.environ.setdefault("TRAFFIC_SCAN_MIN_INTERVAL_SECONDS", "0.20")
os.environ["STABLE_SINGLE_SERVICE_MODE"] = "0"
os.environ["DIST_TRAFFIC_SCAN_BUCKET"] = "date"
os.environ["DIST_TRAFFIC_BROWSER_BUCKET"] = "date-browser"
os.environ["DIST_TRAFFIC_GLOBAL_BUCKET"] = "search-fleet"
os.environ["DIST_TRAFFIC_COOLDOWN_BUCKET"] = "search-fleet"
os.environ["DIST_TRAFFIC_SCAN_LIMIT"] = "4"
os.environ["DIST_TRAFFIC_BROWSER_LIMIT"] = "2"
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = "8"
os.environ["DIST_TRAFFIC_SHARED_COOLDOWN"] = "0"
os.environ["TRAFFIC_MAX_PENALTY_LEVEL"] = "1"
os.environ["TRAFFIC_403_COOLDOWN_SECONDS"] = "0"
os.environ["TRAFFIC_429_COOLDOWN_SECONDS"] = "3"
os.environ["TRAFFIC_MAX_COOLDOWN_SECONDS"] = "3"
os.environ["TRAFFIC_RECOVERY_SUCCESS_COUNT"] = "10"
os.environ["TRAFFIC_RECOVERY_QUIET_SECONDS"] = "10"

from app_version import APP_VERSION
from parser import KleinanzeigenParser, TemporaryAccessError, profile_page_dates, shutdown_shared_browser_runtime
from date_manager import (
    DATE_CACHE_SCHEMA,
    DATE_CACHE_TTL_SECONDS,
    DATE_ERROR_TTL_SECONDS,
    DATE_GROUP,
    DATE_HEARTBEAT_KEY,
    DATE_PENDING_TTL_SECONDS,
    DATE_REDIS_PREFIX,
    DATE_RUNTIME_PREFIX,
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


# v4.4.0: two local consumers per replica, with Redis fleet guards across four
# Railway replicas. This preserves distribution without multiplying site pressure.
DATE_WORKER_CONCURRENCY = _env_int("DATE_WORKER_CONCURRENCY", 2, 1, 4)
DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY = _env_int("DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY", 1, 1, 2)
DATE_WORKER_HEARTBEAT_SECONDS = _env_int("DATE_WORKER_HEARTBEAT_SECONDS", 3, 1, 15)
DATE_WORKER_RECLAIM_IDLE_MS = _env_int("DATE_WORKER_RECLAIM_IDLE_MS", 90_000, 30_000, 300_000)
DATE_WORKER_JOB_TIMEOUT_SECONDS = _env_int("DATE_WORKER_JOB_TIMEOUT_SECONDS", 45, 15, 120)
DATE_WORKER_STATUS_TTL_SECONDS = max(10, DATE_WORKER_HEARTBEAT_SECONDS * 5)


def _assess_probe(info, target_day, *, strict_http: bool):
    """Return (profile, dated_count, safe, reason).

    HTTP probes are deliberately held to a stronger evidence threshold than the
    legacy browser probe. A weak HTTP answer is never cached: the same page is
    confirmed with the browser path. The main bot then performs its own final
    stable boundary verification, so this worker can only accelerate, not decide.
    """
    profile = profile_page_dates(info.items, target_day)
    dated_count = max(0, len(info.items) - int(getattr(info, "missing_date_count", 0) or 0))
    base_safe = (
        bool(getattr(info, "request_matches_page", True))
        and bool(getattr(info, "page_verified", False))
        and not bool(getattr(info, "suspicious", False))
        and profile.relation in {"target", "newer", "older", "mixed", "empty"}
        and (not info.items or dated_count >= 2 or profile.relation == "target")
    )
    if not base_safe:
        return profile, dated_count, False, "structural"
    if not strict_http:
        return profile, dated_count, True, "browser-safe"

    # Empty and mixed pages are useful chronology signals, but too important to
    # accept from the cheap path alone. Browser-confirm them.
    if not info.items:
        return profile, dated_count, False, "http-empty-needs-confirm"
    if profile.relation == "mixed":
        return profile, dated_count, False, "http-mixed-needs-confirm"

    # Directional HTTP evidence needs several exact card dates. Target evidence
    # gets the same treatment unless there are at least two direct target hits.
    if dated_count < 3:
        return profile, dated_count, False, "http-low-dated-evidence"
    if float(getattr(profile, "confidence", 0.0) or 0.0) < 0.30:
        return profile, dated_count, False, "http-low-confidence"
    if profile.relation == "target" and int(getattr(profile, "target_count", 0) or 0) < 2:
        return profile, dated_count, False, "http-single-target-needs-confirm"

    return profile, dated_count, True, "http-high-confidence"


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
        self.http_fast_ok = 0
        self.http_weak = 0
        self.browser_confirms = 0
        self.browser_confirm_ok = 0
        self.transport_conflicts = 0
        self.rate_ema = 0.0
        self._group_ready = False
        self._stop = asyncio.Event()
        # HTTP probes are cheap and parallel. Chromium confirmation is deliberately
        # narrow and also protected by the Redis date-browser fleet bucket.
        self._browser_confirm_sem = asyncio.Semaphore(DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY)

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
        return f"{DATE_REDIS_PREFIX}:cache:{DATE_CACHE_SCHEMA}:{cache_id}"

    def pending_key(self, cache_id: str) -> str:
        return f"{DATE_RUNTIME_PREFIX}:pending:{cache_id}"

    def error_key(self, cache_id: str) -> str:
        return f"{DATE_RUNTIME_PREFIX}:error:{cache_id}"

    def worker_key(self) -> str:
        return f"{DATE_RUNTIME_PREFIX}:worker:{self.base_id}"

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
            "version": APP_VERSION,
            "replica": os.getenv("RAILWAY_REPLICA_ID", "local"),
            "concurrency": DATE_WORKER_CONCURRENCY,
            "browser_confirm_concurrency": DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY,
            "fleet_scan_limit": int(os.environ.get("DIST_TRAFFIC_SCAN_LIMIT", "4")),
            "fleet_browser_limit": int(os.environ.get("DIST_TRAFFIC_BROWSER_LIMIT", "2")),
            "fleet_global_limit": int(os.environ.get("DIST_TRAFFIC_GLOBAL_LIMIT", "8")),
            "fleet_bucket": os.environ.get("DIST_TRAFFIC_GLOBAL_BUCKET", "search-fleet"),
            "active": self.active,
            "queue_depth": queue_depth,
            "processed": self.processed,
            "errors": self.errors,
            "rate_ema": round(self.rate_ema, 3),
            "http_403": self.http_403,
            "http_429": self.http_429,
            "http_first": bool(DATE_WORKER_HTTP_FIRST),
            "http_fast_ok": self.http_fast_ok,
            "http_weak": self.http_weak,
            "browser_confirms": self.browser_confirms,
            "browser_confirm_ok": self.browser_confirm_ok,
            "transport_conflicts": self.transport_conflicts,
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
        # v4.8.3: fresh user work has priority. Crash recovery is attempted only
        # when no new stream message is waiting, so a redeploy cannot spend its
        # first minutes reclaiming stale jobs before serving the current scan.
        rows = await self.redis.xreadgroup(
            DATE_GROUP,
            consumer,
            {DATE_STREAM: ">"},
            count=1,
            block=250,
        )
        if rows and rows[0][1]:
            msg_id, fields = rows[0][1][0]
            return str(msg_id), dict(fields or {})
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
        return None

    async def _ack(self, message_id: str) -> None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.xack(DATE_STREAM, DATE_GROUP, message_id)
        pipe.xdel(DATE_STREAM, message_id)
        await pipe.execute()

    async def consumer_loop(self, index: int) -> None:
        consumer = f"{self.base_id}-{index}"
        parser = KleinanzeigenParser()
        browser_parser = KleinanzeigenParser()
        browser_parser.scan_transport = "browser"
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
                        fallbacks_before = int(getattr(parser, "_hybrid_browser_fallbacks", 0) or 0)
                        info = await asyncio.wait_for(
                            parser.parse_category_page_info(url, requested_page),
                            timeout=DATE_WORKER_JOB_TIMEOUT_SECONDS,
                        )
                        fallbacks_after = int(getattr(parser, "_hybrid_browser_fallbacks", 0) or 0)
                        transport_mode = str(getattr(parser, "scan_transport_status", lambda: parser.scan_transport)())
                        compatibility_browser_used = fallbacks_after > fallbacks_before
                        strict_http = bool(
                            DATE_WORKER_HTTP_FIRST
                            and parser.scan_transport == "hybrid"
                            and not compatibility_browser_used
                        )
                        profile, dated_count, probe_safe, safety_reason = _assess_probe(
                            info, target_day, strict_http=strict_http
                        )
                        source = "http" if strict_http else ("browser-compat" if compatibility_browser_used else "browser")

                        if strict_http and probe_safe:
                            self.http_fast_ok += 1
                        elif strict_http and not probe_safe:
                            # Cheap HTTP evidence was ambiguous. Confirm this exact page
                            # using the normal browser path before allowing it into Redis.
                            # This is a compatibility/quality fallback, not a response to
                            # 403/429 (those raise TemporaryAccessError above and never
                            # reach this branch).
                            self.http_weak += 1
                            self.browser_confirms += 1
                            http_relation = profile.relation
                            async with self._browser_confirm_sem:
                                browser_info = await asyncio.wait_for(
                                    browser_parser.parse_category_page_info(url, requested_page),
                                    timeout=DATE_WORKER_JOB_TIMEOUT_SECONDS,
                                )
                            browser_profile, browser_dated, browser_safe, browser_reason = _assess_probe(
                                browser_info, target_day, strict_http=False
                            )
                            if not browser_safe:
                                self.errors += 1
                                pipe = self.redis.pipeline(transaction=False)
                                pipe.set(
                                    self.error_key(cache_id),
                                    f"weak-date-probe:{safety_reason}:{browser_reason}",
                                    ex=DATE_ERROR_TTL_SECONDS,
                                )
                                pipe.delete(self.pending_key(cache_id), self.cache_key(cache_id))
                                await pipe.execute()
                                log.warning(
                                    "Date Worker rejected weak HTTP+browser probe page=%s http_relation=%s browser_relation=%s http_reason=%s browser_reason=%s",
                                    requested_page, http_relation, browser_profile.relation, safety_reason, browser_reason,
                                )
                                continue
                            if http_relation != browser_profile.relation:
                                self.transport_conflicts += 1
                                log.info(
                                    "Date Worker HTTP/browser chronology changed page=%s http=%s browser=%s; browser hint kept and main bot will revalidate",
                                    requested_page, http_relation, browser_profile.relation,
                                )
                            info = browser_info
                            profile = browser_profile
                            dated_count = browser_dated
                            source = "browser-confirm"
                            transport_mode = "browser-confirm"
                            self.browser_confirm_ok += 1
                        elif not probe_safe:
                            self.errors += 1
                            pipe = self.redis.pipeline(transaction=False)
                            pipe.set(self.error_key(cache_id), f"weak-date-probe:{safety_reason}", ex=DATE_ERROR_TTL_SECONDS)
                            pipe.delete(self.pending_key(cache_id), self.cache_key(cache_id))
                            await pipe.execute()
                            log.warning(
                                "Date Worker rejected weak probe page=%s relation=%s verified=%s matches=%s suspicious=%s dated=%s reason=%s",
                                requested_page, profile.relation,
                                bool(getattr(info, "page_verified", False)),
                                bool(getattr(info, "request_matches_page", True)),
                                bool(getattr(info, "suspicious", False)), dated_count, safety_reason,
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
                            "source": f"date-worker:{source}",
                            "transport_mode": transport_mode,
                            "dated_count": dated_count,
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
            await browser_parser.close()

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
            "DT PARSER Date Worker online | id=%s | replica=%s | http_concurrency=%s | browser_confirm=%s | cache=%ss | HTTP-first=%s",
            self.base_id,
            os.getenv("RAILWAY_REPLICA_ID", "local"),
            DATE_WORKER_CONCURRENCY,
            DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY,
            DATE_CACHE_TTL_SECONDS,
            DATE_WORKER_HTTP_FIRST,
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
