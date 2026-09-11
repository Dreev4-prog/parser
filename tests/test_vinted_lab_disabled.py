from pathlib import Path


BOT = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    return BOT.split(start, 1)[1].split(end, 1)[0]


def test_vinted_lab_is_disabled_by_default_and_hidden_from_admin_menu():
    assert 'os.getenv("VINTED_LAB_ENABLED", "0")' in BOT
    keyboard = _block("def admin_keyboard", "def admin_back_keyboard")
    assert "if VINTED_LAB_ENABLED:" in keyboard
    assert 'text="🟣 Vinted Lab"' in keyboard


def test_old_vinted_callbacks_fail_before_touching_external_state():
    handler = _block("async def vinted_admin_lab_handler", "async def vinted_radar_autoscan_scheduler")
    disabled = handler.index("if not VINTED_LAB_ENABLED:")
    first_action = handler.index('data = str(callback.data or "")')
    assert disabled < first_action
    assert "Vinted Lab временно отключён" in handler


def test_vinted_scheduler_is_not_created_while_lab_is_disabled():
    assert "if VINTED_LAB_ENABLED else None" in BOT
    scheduler = _block("async def vinted_radar_autoscan_scheduler", "# ---- end Vinted Lab")
    assert "if not VINTED_LAB_ENABLED:" in scheduler
