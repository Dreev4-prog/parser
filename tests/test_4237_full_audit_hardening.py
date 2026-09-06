import asyncio
import unittest

from traffic import AdaptiveTrafficManager
from view_manager import RemoteViewManager, RemoteViewResult


class IdleTurboTrafficTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_autoscan_can_borrow_above_normal_view_limit(self):
        traffic = AdaptiveTrafficManager()
        traffic.base_view_limit = 3
        traffic.base_global_limit = 9
        traffic.autoscan_idle_view_limit = 8
        traffic.view_min_interval = 0.0
        await traffic.scan_job_started()  # AutoScan itself.

        entered = 0
        entered_lock = asyncio.Lock()
        all_entered = asyncio.Event()
        release = asyncio.Event()

        async def one():
            nonlocal entered
            async with traffic.lease("view", priority="autoscan_idle"):
                async with entered_lock:
                    entered += 1
                    if entered == 8:
                        all_entered.set()
                await release.wait()

        tasks = [asyncio.create_task(one()) for _ in range(8)]
        await asyncio.wait_for(all_entered.wait(), timeout=1.0)
        snap = await traffic.snapshot()
        self.assertEqual(snap.view_active, 8)
        release.set()
        await asyncio.gather(*tasks)
        await traffic.scan_job_finished()

    async def test_foreground_user_disables_new_idle_burst_leases(self):
        traffic = AdaptiveTrafficManager()
        traffic.base_view_limit = 3
        traffic.base_global_limit = 9
        traffic.reserved_scan_slots = 0
        traffic.autoscan_idle_view_limit = 8
        traffic.view_min_interval = 0.0
        await traffic.scan_job_started()  # AutoScan.
        await traffic.scan_job_started()  # Foreground user.

        entered = 0
        lock = asyncio.Lock()
        three_entered = asyncio.Event()
        release = asyncio.Event()

        async def one():
            nonlocal entered
            async with traffic.lease("view", priority="autoscan_idle"):
                async with lock:
                    entered += 1
                    if entered >= 3:
                        three_entered.set()
                await release.wait()

        tasks = [asyncio.create_task(one()) for _ in range(4)]
        await asyncio.wait_for(three_entered.wait(), timeout=1.0)
        await asyncio.sleep(0.08)
        snap = await traffic.snapshot()
        self.assertEqual(snap.view_active, 3)
        self.assertEqual(entered, 3)
        release.set()
        await asyncio.gather(*tasks)
        await traffic.scan_job_finished()
        await traffic.scan_job_finished()




class RemoteViewDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_sharded_deadline_preserves_completed_results(self):
        manager = RemoteViewManager()
        manager.enabled = True

        async def alive():
            return True

        async def status():
            return {"workers": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]}

        async def fake_batch(urls, **kwargs):
            index = int(kwargs.get("shard_index") or 0)
            if index == 0:
                await asyncio.sleep(0.005)
                return {
                    url: RemoteViewResult(10, "verified-official:test", None, url, None, None)
                    for url in urls
                }
            await asyncio.sleep(1.0)
            return {
                url: RemoteViewResult(20, "verified-official:test", None, url, None, None)
                for url in urls
            }

        manager.worker_alive = alive  # type: ignore[method-assign]
        manager.status = status  # type: ignore[method-assign]
        manager._fetch_single_batch = fake_batch  # type: ignore[method-assign]

        urls = [f"https://kleinanzeigen.de/s-anzeige/test/{100000+i}-1-1" for i in range(40)]
        result = await manager.fetch(urls, deadline_seconds=0.05)
        self.assertIsNotNone(result)
        self.assertGreater(len(result or {}), 0)
        self.assertLess(len(result or {}), len(urls))
        self.assertGreater(manager.partial_shard_fallbacks_total, 0)


if __name__ == "__main__":
    unittest.main()
