from pathlib import Path

BOT = Path(__file__).resolve().parents[1] / "bot.py"
SRC = BOT.read_text(encoding="utf-8")


def test_loading_screen_keeps_autoscan_controls():
    assert 'def admin_radar_autoscan_loading_keyboard()' in SRC
    assert 'text="▶️ Запустить AutoScan"' in SRC
    assert 'callback_data="adminradarauto:start"' in SRC
    assert 'text="⏹ Остановить"' in SRC
    assert 'callback_data="adminradarauto:stop"' in SRC
    assert 'text="🔄 Обновить статистику"' in SRC


def test_radar_snapshot_timeout_does_not_cancel_wait_for_db():
    block = SRC[SRC.index('async def _radar3_dashboard_safe_snapshot'):SRC.index('async def _radar_autoscan_text')]
    assert 'asyncio.shield(task)' in block
    assert 'except asyncio.TimeoutError' in block
    assert 'UI continues with controls' in block


def test_admin_radar_entry_uses_loading_controls_immediately():
    start = SRC.index('async def admin_radar_autoscan_handler')
    end = SRC.index('@dp.callback_query(F.data == "adminradarauto:start")', start)
    block = SRC[start:end]
    assert 'await callback.answer()' in block
    assert 'admin_radar_autoscan_loading_keyboard()' in block
    assert block.index('admin_radar_autoscan_loading_keyboard()') < block.index('_radar_autoscan_safe_text()')
