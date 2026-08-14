from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.joinpath("bot.py").read_text(encoding="utf-8")


def block(start_marker: str, end_marker: str) -> str:
    start = SOURCE.index(start_marker)
    end = SOURCE.index(end_marker, start)
    return SOURCE[start:end]


def test_branded_menu_asset_is_packaged():
    assert ROOT.joinpath("assets", "dt_parser_menu.png").is_file()


def test_home_uses_photo_card_and_local_asset():
    ui = block("async def _send_home_message", "async def _send_popular_message")
    assert "MENU_IMAGE_PATH.exists()" in ui
    assert "answer_photo(" in ui
    assert "FSInputFile(MENU_IMAGE_PATH)" in ui
    assert "caption=caption" in ui


def test_home_keyboard_has_admin_entry_only_when_requested():
    ui = block("def main_keyboard", "def post_scan_keyboard")
    assert "admin: bool = False" in ui
    assert 'text="🛠 Админ-панель"' in ui
    assert 'callback_data="adminhome"' in ui


def test_start_menu_and_home_callbacks_route_to_branded_home():
    ui = block('@dp.message(CommandStart())', '@dp.callback_query(F.data == \"settings\")')
    assert "await _send_home_message(message, message.from_user.id, intro=True)" in ui
    assert '@dp.message(Command("menu"))' in ui
    assert "await _send_home_message(message, message.from_user.id)" in ui
    assert '@dp.callback_query(F.data == "home")' in ui
    assert '@dp.callback_query(F.data == "post_home")' in ui
    assert ui.count("await _send_home_message(callback.message, callback.from_user.id)") >= 2


def test_visible_commands_include_menu_entry():
    ui = block("async def setup_bot_commands", "def onboarding_keyboard")
    assert 'BotCommand(command="menu", description="🏠 Главное меню")' in ui
