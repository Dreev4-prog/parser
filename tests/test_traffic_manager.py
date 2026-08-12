import asyncio
import unittest

from traffic import AdaptiveTrafficManager


class TrafficManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_refusal_reduces_effective_capacity(self):
        tm = AdaptiveTrafficManager()
        before = await tm.snapshot()
        await tm.report_refusal(403, "scan")
        after = await tm.snapshot()
        self.assertEqual(after.penalty_level, 1)
        self.assertLessEqual(after.scan_limit, before.scan_limit)
        self.assertLessEqual(after.view_limit, before.view_limit)
        self.assertGreater(after.cooldown_seconds, 0)

    async def test_background_views_are_limited_while_scan_job_is_active(self):
        tm = AdaptiveTrafficManager()
        tm.background_during_scans = 1
        tm.view_min_interval = 0.0
        tm._cooldown_until = 0.0
        await tm.scan_job_started()
        entered_first = asyncio.Event()
        release_first = asyncio.Event()
        entered_second = asyncio.Event()

        async def first():
            async with tm.lease("view", "background"):
                entered_first.set()
                await release_first.wait()

        async def second():
            async with tm.lease("view", "background"):
                entered_second.set()

        t1 = asyncio.create_task(first())
        await asyncio.wait_for(entered_first.wait(), timeout=1.0)
        t2 = asyncio.create_task(second())
        await asyncio.sleep(0.08)
        self.assertFalse(entered_second.is_set())
        release_first.set()
        await asyncio.wait_for(entered_second.wait(), timeout=1.0)
        await t1
        await t2
        await tm.scan_job_finished()

    async def test_scan_capacity_is_reserved_from_background_work(self):
        tm = AdaptiveTrafficManager()
        tm.base_global_limit = 4
        tm.reserved_scan_slots = 2
        tm.background_during_scans = 4
        tm.view_min_interval = 0.0
        tm.scan_min_interval = 0.0
        await tm.scan_job_started()
        holders = []
        entered = 0
        lock = asyncio.Lock()
        release = asyncio.Event()

        async def bg():
            nonlocal entered
            async with tm.lease("view", "background"):
                async with lock:
                    entered += 1
                await release.wait()

        for _ in range(4):
            holders.append(asyncio.create_task(bg()))
        await asyncio.sleep(0.12)
        self.assertLessEqual(entered, 2)

        scan_entered = asyncio.Event()
        async def scan():
            async with tm.lease("scan", "high"):
                scan_entered.set()

        st = asyncio.create_task(scan())
        await asyncio.wait_for(scan_entered.wait(), timeout=1.0)
        release.set()
        await asyncio.gather(*holders, st)
        await tm.scan_job_finished()


if __name__ == "__main__":
    unittest.main()
