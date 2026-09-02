from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    return BOT.split(start, 1)[1].split(end, 1)[0]


def test_active_home_has_two_equal_full_width_primary_actions():
    block = _block("def main_keyboard(", "def post_scan_keyboard(")
    scan = '[InlineKeyboardButton(text="▶️ НОВЫЙ СКАН", callback_data="start_scan")]'
    radar = '[InlineKeyboardButton(text="📡 DT RADAR 3.0", callback_data="radar_home")]'
    popular = 'InlineKeyboardButton(text="🔥 Популярное", callback_data="popular_now")'
    scans = 'InlineKeyboardButton(text="📊 Мои сканы", callback_data="my_scans")'
    assert scan in block
    assert radar in block
    assert block.index(scan) < block.index(radar) < block.index(popular)
    assert popular in block and scans in block


def test_radar_is_also_full_width_for_trial_and_expired_states():
    block = _block("def main_keyboard(", "def post_scan_keyboard(")
    assert block.count('[InlineKeyboardButton(text="📡 DT RADAR 3.0 · 🎁", callback_data="radar_home")]') == 2


def test_active_home_copy_positions_scan_and_radar_as_equal_products():
    block = _block("def home_text(", "async def _send_home_message(")
    assert "📡 DT PARSER — MARKET ANALYTICS" in block
    assert "Сканируй рынок. Находи спрос." in block
    assert "🔎 <b>Сканирование</b>" in block
    assert "📡 <b>DT Radar 3.0</b>" in block
    assert "нестандартную товарку с растущим интересом" in block
    assert "<b>Выбери режим 👇</b>" in block
    assert "<b>Перед новым сканом:</b>" not in block


def test_home_ui_patch_does_not_change_primary_callbacks():
    block = _block("def main_keyboard(", "def post_scan_keyboard(")
    for callback in ("start_scan", "radar_home", "popular_now", "my_scans", "groups", "settings", "queue_status", "auto_obs_menu", "subscription"):
        assert f'callback_data="{callback}"' in block
