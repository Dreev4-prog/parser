from __future__ import annotations

import asyncio
import os
import time
import logging
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass

from distributed import COORDINATOR, DISTRIBUTED_WORKERS, DIST_TRAFFIC_SHARED_COOLDOWN

log = logging.getLogger("dtparser-traffic")


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


@dataclass(frozen=True)
class TrafficSnapshot:
    scan_jobs_active: int
    scan_active: int
    view_active: int
    browser_active: int
    background_view_active: int
    scan_limit: int
    view_limit: int
    browser_limit: int
    global_limit: int
    penalty_level: int
    cooldown_seconds: float
    refusals_60s: int
    total_successes: int
    total_refusals: int
    lease_acquires: int
    local_wait_seconds_total: float
    distributed_wait_seconds_total: float


class AdaptiveTrafficManager:
    """One process-wide traffic controller for every Kleinanzeigen request.

    It does not try to bypass site protection. It simply smooths bursts, reserves
    capacity for interactive scans, slows down low-priority view checkpoints while
    scans are active, and backs off globally after 403/429 responses.
    """

    def __init__(self) -> None:
        self.base_scan_limit = _env_int("TRAFFIC_SCAN_CONCURRENCY", 5, 1, 12)
        self.base_view_limit = _env_int("TRAFFIC_VIEW_CONCURRENCY", 3, 1, 24)
        self.base_browser_limit = _env_int("TRAFFIC_BROWSER_CONCURRENCY", 1, 1, 4)
        self.base_global_limit = _env_int("TRAFFIC_GLOBAL_CONCURRENCY", 9, 2, 32)
        self.background_during_scans = _env_int("TRAFFIC_BACKGROUND_VIEWS_DURING_SCANS", 1, 0, 6)
        self.reserved_scan_slots = _env_int("TRAFFIC_RESERVED_SCAN_SLOTS", 4, 0, 8)

        self.scan_min_interval = _env_float("TRAFFIC_SCAN_MIN_INTERVAL_SECONDS", 0.55, 0.0, 5.0)
        self.view_min_interval = _env_float("TRAFFIC_VIEW_MIN_INTERVAL_SECONDS", 0.20, 0.0, 2.0)
        self.browser_min_interval = _env_float("TRAFFIC_BROWSER_MIN_INTERVAL_SECONDS", 0.75, 0.0, 10.0)
        self.base_cooldown = _env_float("TRAFFIC_403_COOLDOWN_SECONDS", 8.0, 1.0, 60.0)
        self.max_cooldown = _env_float("TRAFFIC_MAX_COOLDOWN_SECONDS", 60.0, 5.0, 240.0)
        self.recovery_successes = _env_int("TRAFFIC_RECOVERY_SUCCESS_COUNT", 60, 10, 1000)
        self.recovery_quiet_seconds = _env_float("TRAFFIC_RECOVERY_QUIET_SECONDS", 60.0, 10.0, 600.0)

        self._condition = asyncio.Condition()
        self._active = {"scan": 0, "view": 0, "browser": 0}
        self._background_view_active = 0
        self._scan_jobs_active = 0
        self._next_allowed = {"scan": 0.0, "view": 0.0, "browser": 0.0}
        self._cooldown_until = 0.0
        self._penalty = 0
        self._refusals: deque[float] = deque(maxlen=200)
        self._last_refusal = 0.0
        self._last_recovery = 0.0
        self._success_since_penalty = 0
        self._total_successes = 0
        self._total_refusals = 0
        self._lease_acquires = 0
        self._local_wait_seconds_total = 0.0
        self._distributed_wait_seconds_total = 0.0

    def _effective_limits(self) -> tuple[int, int, int, int]:
        penalty = self._penalty
        scan = max(1, self.base_scan_limit - penalty)
        view = max(2 if self.base_view_limit >= 2 else 1, self.base_view_limit - penalty * 2)
        # Foreground category pages are more valuable than view enrichment when
        # several people scan at once.  Keep a small view lane alive, but never let
        # it fill the process-wide pool and make all five users look frozen.
        if self._scan_jobs_active >= 4:
            view = min(view, 2)
        browser = max(1, self.base_browser_limit - (1 if penalty >= 2 else 0))
        global_limit = max(2, self.base_global_limit - penalty * 2)
        return scan, view, browser, global_limit

    def _kind_interval(self, kind: str) -> float:
        if kind == "scan":
            return self.scan_min_interval
        if kind == "browser":
            return self.browser_min_interval
        # v4.3.3 Adaptive Fast View Lane. Healthy official-counter traffic can run
        # at the faster configured cadence. Any 403/429 penalty immediately makes
        # the cadence progressively more conservative after the global cooldown,
        # in addition to the existing concurrency reduction.
        return min(2.0, self.view_min_interval * (2 ** self._penalty))

    def _prune_refusals(self, now: float) -> None:
        while self._refusals and now - self._refusals[0] > 60.0:
            self._refusals.popleft()

    def _maybe_recover_locked(self, now: float) -> None:
        if self._penalty <= 0:
            return
        quiet = now - self._last_refusal
        if quiet < self.recovery_quiet_seconds:
            return
        if self._success_since_penalty < self.recovery_successes:
            return
        if now - self._last_recovery < self.recovery_quiet_seconds:
            return
        self._penalty -= 1
        self._success_since_penalty = 0
        self._last_recovery = now

    async def scan_job_started(self) -> None:
        async with self._condition:
            self._scan_jobs_active += 1
            self._condition.notify_all()

    async def scan_job_finished(self) -> None:
        async with self._condition:
            self._scan_jobs_active = max(0, self._scan_jobs_active - 1)
            self._condition.notify_all()

    async def report_success(self, kind: str) -> None:
        now = time.monotonic()
        async with self._condition:
            self._total_successes += 1
            self._success_since_penalty += 1
            self._maybe_recover_locked(now)
            self._condition.notify_all()

    async def report_refusal(self, status_code: int, kind: str) -> None:
        if int(status_code) not in {403, 429}:
            return
        now = time.monotonic()
        async with self._condition:
            self._total_refusals += 1
            self._refusals.append(now)
            self._last_refusal = now
            self._success_since_penalty = 0
            self._prune_refusals(now)

            # One refusal already lowers concurrency. A cluster makes the cooldown
            # progressively longer, but it remains bounded and self-recovers.
            self._penalty = min(3, self._penalty + 1)
            cluster = max(1, len(self._refusals))
            cooldown = min(self.max_cooldown, self.base_cooldown * (2 ** (self._penalty - 1)))
            if cluster >= 3:
                cooldown = min(self.max_cooldown, cooldown * 1.5)
            self._cooldown_until = max(self._cooldown_until, now + cooldown)
            self._condition.notify_all()
        if DISTRIBUTED_WORKERS and DIST_TRAFFIC_SHARED_COOLDOWN:
            try:
                await COORDINATOR.report_traffic_refusal(int(status_code))
            except Exception:
                log.debug("Could not publish distributed traffic cooldown", exc_info=True)

    async def snapshot(self) -> TrafficSnapshot:
        now = time.monotonic()
        async with self._condition:
            self._prune_refusals(now)
            self._maybe_recover_locked(now)
            scan_limit, view_limit, browser_limit, global_limit = self._effective_limits()
            return TrafficSnapshot(
                scan_jobs_active=self._scan_jobs_active,
                scan_active=self._active["scan"],
                view_active=self._active["view"],
                browser_active=self._active["browser"],
                background_view_active=self._background_view_active,
                scan_limit=scan_limit,
                view_limit=view_limit,
                browser_limit=browser_limit,
                global_limit=global_limit,
                penalty_level=self._penalty,
                cooldown_seconds=max(0.0, self._cooldown_until - now),
                refusals_60s=len(self._refusals),
                total_successes=self._total_successes,
                total_refusals=self._total_refusals,
                lease_acquires=self._lease_acquires,
                local_wait_seconds_total=self._local_wait_seconds_total,
                distributed_wait_seconds_total=self._distributed_wait_seconds_total,
            )

    @asynccontextmanager
    async def lease(self, kind: str, priority: str = "normal"):
        if kind not in {"scan", "view", "browser"}:
            raise ValueError(f"Unknown traffic kind: {kind}")
        is_background = kind == "view" and priority == "background"
        acquired = False
        lease_started = time.monotonic()
        local_acquired_at = lease_started
        try:
            while not acquired:
                async with self._condition:
                    now = time.monotonic()
                    self._prune_refusals(now)
                    self._maybe_recover_locked(now)
                    scan_limit, view_limit, browser_limit, global_limit = self._effective_limits()
                    per_kind_limit = {
                        "scan": scan_limit,
                        "view": view_limit,
                        "browser": browser_limit,
                    }[kind]
                    total_active = sum(self._active.values())

                    cooldown_wait = max(0.0, self._cooldown_until - now)
                    spacing_wait = max(0.0, self._next_allowed[kind] - now)
                    background_ok = True
                    if is_background and self._scan_jobs_active > 0:
                        background_ok = self._background_view_active < self.background_during_scans

                    # Reserve part of the global pool for interactive category-page
                    # work while any scan job is alive. This prevents a large view
                    # checkpoint from filling every network slot just before a scan.
                    global_cap = global_limit
                    if kind != "scan" and self._scan_jobs_active > 0:
                        global_cap = max(1, global_limit - self.reserved_scan_slots)

                    can_acquire = (
                        cooldown_wait <= 0.0
                        and spacing_wait <= 0.0
                        and self._active[kind] < per_kind_limit
                        and total_active < global_cap
                        and background_ok
                    )
                    if can_acquire:
                        self._active[kind] += 1
                        if is_background:
                            self._background_view_active += 1
                        self._next_allowed[kind] = now + self._kind_interval(kind)
                        acquired = True
                        local_acquired_at = time.monotonic()
                        break

                    waits = [0.25]
                    if cooldown_wait > 0:
                        waits.append(min(cooldown_wait, 2.0))
                    if spacing_wait > 0:
                        waits.append(min(spacing_wait, 0.5))
                    timeout = max(0.03, min(waits))
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=timeout)
                    except asyncio.TimeoutError:
                        pass
            distributed_token = None
            distributed_started = time.monotonic()
            if DISTRIBUTED_WORKERS:
                try:
                    distributed_token = await COORDINATOR.acquire_traffic(
                        kind, interval_seconds=self._kind_interval(kind) / 2.0
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Redis queue/cache may still be healthy while a transient limiter
                    # command fails. Keep the local safety limits rather than taking
                    # every worker offline.
                    log.warning("Distributed traffic limiter unavailable; using local limit", exc_info=True)
            distributed_done = time.monotonic()
            async with self._condition:
                self._lease_acquires += 1
                self._local_wait_seconds_total += max(0.0, local_acquired_at - lease_started)
                if DISTRIBUTED_WORKERS:
                    self._distributed_wait_seconds_total += max(0.0, distributed_done - distributed_started)
            try:
                yield
            finally:
                if distributed_token is not None:
                    try:
                        await COORDINATOR.release_traffic(kind, distributed_token)
                    except Exception:
                        log.debug("Could not release distributed traffic token", exc_info=True)
        finally:
            if acquired:
                async with self._condition:
                    self._active[kind] = max(0, self._active[kind] - 1)
                    if is_background:
                        self._background_view_active = max(0, self._background_view_active - 1)
                    self._condition.notify_all()


TRAFFIC = AdaptiveTrafficManager()
