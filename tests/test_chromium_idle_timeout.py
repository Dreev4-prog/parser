import asyncio
import unittest
from unittest.mock import patch

import parser


class _FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class ChromiumIdleTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_chromium_closes_only_after_full_idle_interval(self):
        runtime = parser._SharedBrowserRuntime()
        browser = _FakeBrowser()
        playwright = _FakePlaywright()
        runtime._browser = browser
        runtime._playwright = playwright

        with patch.object(parser, "BROWSER_IDLE_TIMEOUT_SECONDS", 0.05):
            runtime.touch()
            await asyncio.sleep(0.03)
            runtime.touch()
            await asyncio.sleep(0.03)
            self.assertFalse(browser.closed, "new browser work must postpone idle shutdown")
            await asyncio.sleep(0.05)

        self.assertTrue(browser.closed)
        self.assertTrue(playwright.stopped)
        self.assertEqual(runtime.generation, 1)


if __name__ == "__main__":
    unittest.main()
