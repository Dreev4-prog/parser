from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

from parser import shared_browser_runtime_running, shutdown_shared_browser_runtime


class BrowserIdleShutdownGuard:
    """Close process-local shared Chromium only after the whole parser is idle.

    Both the local worker stream and the global foreground scan stream must be empty.
    The global scan entry is deleted only after the full user scan finishes, preventing
    a Page/View browser from shutting down merely because another scan stage is active.

    The activity_lock closes the last race: a consumer cannot begin a new browser job
    between the final idle check and Chromium shutdown. If a job arrives during the
    shutdown, it waits briefly and then lazily recreates Chromium.
    """

    def __init__(
        self,
        *,
        redis,
        stream: str,
        active_count: Callable[[], int],
        activity_lock: asyncio.Lock,
        stop_event: asyncio.Event,
        idle_seconds: int = 600,
        poll_seconds: float = 5.0,
        label: str = "worker",
        logger: logging.Logger | None = None,
        global_scan_stream: str | None = None,
    ) -> None:
        self.redis = redis
        self.stream = str(stream)
        redis_prefix = (os.getenv("REDIS_PREFIX", "dtparser").strip() or "dtparser")
        self.global_scan_stream = str(global_scan_stream or f"{redis_prefix}:scan_jobs")
        self.active_count = active_count
        self.activity_lock = activity_lock
        self.stop_event = stop_event
        self.idle_seconds = max(60, int(idle_seconds))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.label = str(label)
        self.log = logger or logging.getLogger("dtparser-browser-idle")
        self._idle_since: float | None = None
        self._activity_generation = 0

    def touch(self) -> None:
        """Reset the idle timer for non-job warmup activity.

        The generation also closes a subtle race where a prewarm starts while
        tick() is already waiting to enter the final shutdown lock.
        """
        self._activity_generation += 1
        self._idle_since = None

    async def _queue_depths(self) -> tuple[int | None, int | None]:
        """Return local worker depth and global foreground-scan depth in one RTT.

        The original v4.6.1 guard only watched the worker's own stream. That meant
        Page/View Chromium could enter its 10-minute countdown while a user scan was
        still busy in another stage. The global scan stream keeps its entry until the
        parser worker finishes the whole scan, so it is the correct system-wide busy
        signal for memory shutdown.
        """
        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.xlen(self.stream)
            pipe.xlen(self.global_scan_stream)
            local_depth, global_depth = await pipe.execute()
            return int(local_depth), int(global_depth)
        except Exception:
            # Safety first: unknown queue state must never trigger browser close.
            return None, None

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_seconds)
        except asyncio.TimeoutError:
            pass

    async def tick(self) -> bool:
        """Run one safe idle-state transition. Returns True only on shutdown."""
        if not shared_browser_runtime_running():
            self._idle_since = None
            return False

        if int(self.active_count() or 0) > 0:
            self._idle_since = None
            return False

        depth, global_depth = await self._queue_depths()
        if depth is None or global_depth is None or depth > 0 or global_depth > 0:
            self._idle_since = None
            return False

        now = time.monotonic()
        if self._idle_since is None:
            self._idle_since = now
            self.log.info(
                "%s Chromium warm-idle countdown started | timeout=%ss",
                self.label,
                self.idle_seconds,
            )
            return False

        if now - self._idle_since < self.idle_seconds:
            return False

        closed = False
        # Serialize the final transition with job activation. Re-check both local
        # work and the Redis stream while holding the lock. A prewarm can happen
        # while this task is waiting for the lock, so also verify its generation.
        activity_generation = self._activity_generation
        async with self.activity_lock:
            depth, global_depth = await self._queue_depths()
            if self._activity_generation != activity_generation:
                self._idle_since = None
                return False
            if (
                int(self.active_count() or 0) == 0
                and depth == 0
                and global_depth == 0
                and shared_browser_runtime_running()
            ):
                await shutdown_shared_browser_runtime()
                closed = True
                self.log.info(
                    "%s Chromium closed after %ss of complete fleet idle",
                    self.label,
                    self.idle_seconds,
                )
        self._idle_since = None
        return closed

    async def run(self) -> None:
        while not self.stop_event.is_set():
            await self.tick()
            await self._wait()
