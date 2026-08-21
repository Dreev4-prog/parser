from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from parser import shared_browser_runtime_running, shutdown_shared_browser_runtime


class BrowserIdleShutdownGuard:
    """Close process-local shared Chromium only after the whole worker fleet is idle.

    Redis stream entries remain present while queued *and* while claimed/processing,
    because workers XDEL them only after acknowledgement. Therefore xlen(stream)==0
    plus local active==0 is a conservative signal that this worker type has no work.

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
    ) -> None:
        self.redis = redis
        self.stream = str(stream)
        self.active_count = active_count
        self.activity_lock = activity_lock
        self.stop_event = stop_event
        self.idle_seconds = max(60, int(idle_seconds))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.label = str(label)
        self.log = logger or logging.getLogger("dtparser-browser-idle")
        self._idle_since: float | None = None

    async def _queue_depth(self) -> int | None:
        try:
            return int(await self.redis.xlen(self.stream))
        except Exception:
            # Safety first: an unknown queue state must never trigger browser close.
            return None

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

        depth = await self._queue_depth()
        if depth is None or depth > 0:
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
        # work and the Redis stream while holding the lock.
        async with self.activity_lock:
            depth = await self._queue_depth()
            if (
                int(self.active_count() or 0) == 0
                and depth == 0
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
