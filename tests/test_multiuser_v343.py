from pathlib import Path

BOT_SOURCE = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text(encoding="utf-8")
TRAFFIC_SOURCE = Path(__file__).resolve().parents[1].joinpath("traffic.py").read_text(encoding="utf-8")
ENV_SOURCE = Path(__file__).resolve().parents[1].joinpath(".env.example").read_text(encoding="utf-8")


def test_default_worker_profile_targets_five_simultaneous_users():
    assert 'MAX_CONCURRENT_JOBS", "5"' in BOT_SOURCE
    assert 'TRAFFIC_SCAN_CONCURRENCY", 5' in TRAFFIC_SOURCE
    assert 'TRAFFIC_GLOBAL_CONCURRENCY", 9' in TRAFFIC_SOURCE
    assert 'TRAFFIC_RESERVED_SCAN_SLOTS", 4' in TRAFFIC_SOURCE
    assert 'MAX_CONCURRENT_JOBS=5' in ENV_SOURCE


def test_view_lane_yields_when_four_or_more_scans_are_alive():
    assert 'if self._scan_jobs_active >= 4:' in TRAFFIC_SOURCE
    assert 'view = min(view, 2)' in TRAFFIC_SOURCE
    assert 'TRAFFIC_VIEW_MIN_INTERVAL_SECONDS=0.20' in ENV_SOURCE


def test_date_locator_has_visible_percentage_and_real_request_heartbeat():
    assert 'Поиск даты · {percent}%' in BOT_SOURCE
    assert 'Проверено запросов' in BOT_SOURCE
    assert 'network_requests: int = 0' in BOT_SOURCE

import asyncio
from traffic import AdaptiveTrafficManager


def test_five_scan_leases_can_coexist_under_balanced_profile():
    async def scenario():
        tm = AdaptiveTrafficManager()
        tm.base_scan_limit = 5
        tm.base_global_limit = 9
        tm.scan_min_interval = 0.0
        tm._cooldown_until = 0.0
        entered = 0
        lock = asyncio.Lock()
        all_entered = asyncio.Event()
        release = asyncio.Event()

        async def worker():
            nonlocal entered
            async with tm.lease("scan", "high"):
                async with lock:
                    entered += 1
                    if entered == 5:
                        all_entered.set()
                await release.wait()

        tasks = [asyncio.create_task(worker()) for _ in range(5)]
        await asyncio.wait_for(all_entered.wait(), timeout=1.0)
        release.set()
        await asyncio.gather(*tasks)
        assert entered == 5

    asyncio.run(scenario())
