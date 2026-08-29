from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / 'bot.py').read_text()


def test_adminai_opens_loading_panel_before_stats():
    start = BOT.index('@dp.callback_query(F.data == "adminai")')
    end = BOT.index('@dp.callback_query(F.data.startswith("adminai:"))', start)
    block = BOT[start:end]
    loading = block.index('_admin_ai_dashboard_loading_text()')
    safe = block.index('_admin_ai_dashboard_safe_text()')
    assert loading < safe
    assert '_admin_ai_dashboard_text()' not in block


def test_stats_have_timeout_and_visible_fallback():
    start = BOT.index('async def _admin_ai_dashboard_safe_text')
    end = BOT.index('async def _ai_candidate_rows', start)
    block = BOT[start:end]
    assert 'asyncio.wait_for' in block
    assert 'asyncio.TimeoutError' in block
    assert 'Панель открыта: <b>✅</b>' in block
    assert 'Статистика: <b>⚠️ временно недоступна</b>' in block


def test_stale_ai_routes_are_nonblocking_too():
    start = BOT.index('@dp.callback_query(F.data.startswith("adminai:"))')
    end = BOT.index('@dp.callback_query(F.data == "adminviews")', start)
    block = BOT[start:end]
    assert block.count('_admin_ai_dashboard_loading_text()') >= 2
    assert block.count('_admin_ai_dashboard_safe_text()') >= 2
