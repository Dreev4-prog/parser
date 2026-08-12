import asyncio

from parser import KleinanzeigenParser, ViewCountResult


def test_mass_view_refresh_is_direct_http_only_by_default(monkeypatch):
    async def run():
        parser = KleinanzeigenParser()
        direct_calls = []
        browser_calls = []

        async def fake_direct(url, *, traffic_priority="normal"):
            direct_calls.append(url)
            return ViewCountResult(123, "123", "direct-http:test")

        async def fake_browser(*args, **kwargs):
            browser_calls.append(args[0] if args else "")
            return ViewCountResult(999, "999", "browser:test")

        monkeypatch.setattr(parser, "_direct_view_http", fake_direct)
        monkeypatch.setattr(parser, "fetch_public_view_count", fake_browser)
        try:
            urls = [
                "https://www.kleinanzeigen.de/s-anzeige/a/111-1-1",
                "https://www.kleinanzeigen.de/s-anzeige/b/222-1-1",
            ]
            result = await parser.fetch_public_view_counts(urls, concurrency=2, batch_size=1, batch_pause_seconds=0)
            assert len(direct_calls) == 2
            assert browser_calls == []
            assert all(v.views == 123 for v in result.values())
        finally:
            await parser.close()

    asyncio.run(run())


def test_failed_direct_does_not_trigger_browser_by_default(monkeypatch):
    async def run():
        parser = KleinanzeigenParser()
        browser_calls = []

        async def fake_direct(url, *, traffic_priority="normal"):
            return ViewCountResult(None, None, "direct-http:test-failed")

        async def fake_browser(*args, **kwargs):
            browser_calls.append(True)
            return ViewCountResult(321, "321", "browser:test")

        monkeypatch.setattr(parser, "_direct_view_http", fake_direct)
        monkeypatch.setattr(parser, "fetch_public_view_count", fake_browser)
        try:
            url = "https://www.kleinanzeigen.de/s-anzeige/a/111-1-1"
            result = await parser.fetch_public_view_counts([url], batch_pause_seconds=0)
            assert result[url].views is None
            assert browser_calls == []
        finally:
            await parser.close()

    asyncio.run(run())
