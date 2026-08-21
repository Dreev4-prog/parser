import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import browser_idle
from browser_idle import BrowserIdleShutdownGuard


class FakeRedis:
    def __init__(self, depth=0):
        self.depth = depth

    async def xlen(self, _stream):
        return self.depth


class BrowserIdleShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_closes_only_after_full_idle_timeout(self):
        redis = FakeRedis(0)
        active = {"n": 0}
        shutdown = AsyncMock()
        guard = BrowserIdleShutdownGuard(
            redis=redis,
            stream="jobs",
            active_count=lambda: active["n"],
            activity_lock=asyncio.Lock(),
            stop_event=asyncio.Event(),
            idle_seconds=600,
            poll_seconds=1,
            label="test",
        )
        with patch.object(browser_idle, "shared_browser_runtime_running", return_value=True), \
             patch.object(browser_idle, "shutdown_shared_browser_runtime", shutdown), \
             patch.object(browser_idle.time, "monotonic", side_effect=[100.0, 699.0, 701.0]):
            self.assertFalse(await guard.tick())
            self.assertFalse(await guard.tick())
            self.assertTrue(await guard.tick())
        shutdown.assert_awaited_once()

    async def test_active_job_cancels_countdown(self):
        redis = FakeRedis(0)
        active = {"n": 0}
        shutdown = AsyncMock()
        guard = BrowserIdleShutdownGuard(
            redis=redis,
            stream="jobs",
            active_count=lambda: active["n"],
            activity_lock=asyncio.Lock(),
            stop_event=asyncio.Event(),
            idle_seconds=600,
            label="test",
        )
        with patch.object(browser_idle, "shared_browser_runtime_running", return_value=True), \
             patch.object(browser_idle, "shutdown_shared_browser_runtime", shutdown), \
             patch.object(browser_idle.time, "monotonic", side_effect=[100.0, 500.0, 1050.0]):
            self.assertFalse(await guard.tick())
            active["n"] = 1
            self.assertFalse(await guard.tick())
            active["n"] = 0
            self.assertFalse(await guard.tick())
        shutdown.assert_not_awaited()

    async def test_queued_or_claimed_stream_entry_prevents_countdown(self):
        redis = FakeRedis(1)
        shutdown = AsyncMock()
        guard = BrowserIdleShutdownGuard(
            redis=redis,
            stream="jobs",
            active_count=lambda: 0,
            activity_lock=asyncio.Lock(),
            stop_event=asyncio.Event(),
            idle_seconds=600,
            label="test",
        )
        with patch.object(browser_idle, "shared_browser_runtime_running", return_value=True), \
             patch.object(browser_idle, "shutdown_shared_browser_runtime", shutdown):
            self.assertFalse(await guard.tick())
            self.assertIsNone(guard._idle_since)
        shutdown.assert_not_awaited()

    async def test_unknown_redis_state_never_closes_browser(self):
        class BrokenRedis:
            async def xlen(self, _stream):
                raise RuntimeError("redis unavailable")

        shutdown = AsyncMock()
        guard = BrowserIdleShutdownGuard(
            redis=BrokenRedis(),
            stream="jobs",
            active_count=lambda: 0,
            activity_lock=asyncio.Lock(),
            stop_event=asyncio.Event(),
            idle_seconds=60,
            label="test",
        )
        guard._idle_since = 0.0
        with patch.object(browser_idle, "shared_browser_runtime_running", return_value=True), \
             patch.object(browser_idle, "shutdown_shared_browser_runtime", shutdown):
            self.assertFalse(await guard.tick())
        shutdown.assert_not_awaited()
        self.assertIsNone(guard._idle_since)


if __name__ == "__main__":
    unittest.main()
