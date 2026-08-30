from pathlib import Path

BOT = Path(__file__).resolve().parents[1] / "bot.py"
SRC = BOT.read_text(encoding="utf-8")


def test_emergency_screen_keeps_autoscan_controls():
    assert 'def admin_radar_autoscan_loading_keyboard()' in SRC
    assert 'text="▶️ Запустить AutoScan"' in SRC
    assert 'callback_data="adminradarauto:start"' in SRC
    assert 'text="⏹ Остановить"' in SRC
    assert 'callback_data="adminradarauto:stop"' in SRC
    assert 'text="🔄 Обновить Live"' in SRC


def test_radar_snapshot_timeout_does_not_cancel_wait_for_db():
    block = SRC[SRC.index('async def _radar3_dashboard_safe_snapshot'):SRC.index('async def _radar_autoscan_text')]
    assert 'asyncio.shield(task)' in block
    assert 'except asyncio.TimeoutError' in block
    assert 'UI continues with controls' in block


def test_admin_radar_entry_builds_lightweight_live_panel_only():
    start = SRC.index('async def admin_radar_autoscan_handler')
    end = SRC.index('@dp.callback_query(F.data == "adminradarauto:analytics")', start)
    block = SRC[start:end]
    assert 'await callback.answer()' in block
    assert 'asyncio.wait_for(_radar_autoscan_text(), timeout=2.0)' in block
    assert '_radar3_dashboard_snapshot' not in block
    assert '_radar3_dashboard_safe_snapshot' not in block


def test_deep_analytics_is_separate_callback():
    assert '@dp.callback_query(F.data == "adminradarauto:analytics")' in SRC
    assert 'text="📊 Аналитика Radar"' in SRC
    live = SRC[SRC.index('async def _radar_autoscan_text'):SRC.index('async def _radar3_analytics_text')]
    assert '_radar3_dashboard_safe_snapshot' not in live
