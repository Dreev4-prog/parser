import asyncio
import unittest

from scan_control import ScanStopRequested, wait_for_task_or_stop


class ScanControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_detaches_without_cancelling_shared_task(self):
        release = asyncio.Event()
        stop = asyncio.Event()

        async def work():
            await release.wait()
            return 42

        task = asyncio.create_task(work())
        waiter = asyncio.create_task(wait_for_task_or_stop(task, stop))
        await asyncio.sleep(0)
        stop.set()
        with self.assertRaises(ScanStopRequested):
            await asyncio.wait_for(waiter, timeout=1)
        self.assertFalse(task.cancelled())
        self.assertFalse(task.done())
        release.set()
        self.assertEqual(await task, 42)

    async def test_completed_task_returns_result(self):
        stop = asyncio.Event()
        task = asyncio.create_task(asyncio.sleep(0.01, result="ok"))
        result = await wait_for_task_or_stop(task, stop)
        self.assertEqual(result, "ok")

    async def test_already_stopped_never_waits_for_task(self):
        stop = asyncio.Event()
        stop.set()
        task = asyncio.create_task(asyncio.sleep(5))
        try:
            with self.assertRaises(ScanStopRequested):
                await wait_for_task_or_stop(task, stop)
            self.assertFalse(task.done())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
