import asyncio
import unittest
from unittest.mock import AsyncMock

import view_manager
from view_manager import RemoteViewManager, RemoteViewResult


class ViewPartialShardTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_failed_shard_preserves_other_results(self):
        manager = RemoteViewManager()
        manager.enabled = True
        manager.worker_alive = AsyncMock(return_value=True)
        manager.status = AsyncMock(return_value={"workers": [{}, {}, {}, {}]})

        async def fake_batch(urls, **kwargs):
            if kwargs.get("shard_index") == 2:
                return None
            return {
                url: RemoteViewResult(views=123, source="test")
                for url in urls
            }

        manager._fetch_single_batch = fake_batch  # type: ignore[method-assign]
        urls = [f"https://example.invalid/{i}" for i in range(max(view_manager.VIEW_SHARD_MIN_URLS, 320))]
        result = await manager.fetch(urls)

        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        self.assertLess(len(result), len(urls))
        self.assertEqual(manager.last_shard_failed, 1)
        self.assertEqual(manager.partial_shard_fallbacks_total, 1)

    async def test_concurrent_batches_do_not_share_failure_decision(self):
        manager = RemoteViewManager()
        manager.enabled = True
        manager.worker_alive = AsyncMock(return_value=True)
        manager.status = AsyncMock(return_value={"workers": [{}, {}, {}, {}]})

        async def fake_batch(urls, **kwargs):
            await asyncio.sleep(0)
            is_failing_parent = any("/fail/" in url for url in urls)
            if is_failing_parent and kwargs.get("shard_index") == 2:
                return None
            return {url: RemoteViewResult(views=321, source="test") for url in urls}

        manager._fetch_single_batch = fake_batch  # type: ignore[method-assign]
        total = max(view_manager.VIEW_SHARD_MIN_URLS, 320)
        good_urls = [f"https://example.invalid/good/{i}" for i in range(total)]
        fail_urls = [f"https://example.invalid/fail/{i}" for i in range(total)]
        good_progress: list[tuple[int, int]] = []

        async def good_cb(done: int, count: int):
            good_progress.append((done, count))

        good_result, fail_result = await asyncio.gather(
            manager.fetch(good_urls, progress_cb=good_cb),
            manager.fetch(fail_urls),
        )

        self.assertEqual(len(good_result or {}), total)
        self.assertLess(len(fail_result or {}), total)
        self.assertTrue(good_progress)
        self.assertEqual(good_progress[-1], (total, total))


if __name__ == "__main__":
    unittest.main()
