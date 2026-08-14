from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text(encoding="utf-8")


def block(start_marker: str, end_marker: str) -> str:
    start = SOURCE.index(start_marker)
    end = SOURCE.index(end_marker, start)
    return SOURCE[start:end]


def test_home_has_one_primary_action_and_no_permanent_current_result():
    ui = block("def main_keyboard", "def post_scan_keyboard")
    assert 'text="▶️ Новый скан"' in ui
    assert 'text="🔥 Популярное"' in ui
    assert 'text="📊 Мои сканы"' in ui
    assert "Текущий результат" not in ui
    assert ui.count('callback_data="start_scan"') == 1


def test_settings_are_compact_instead_of_showing_all_modes_at_once():
    ui = block("def settings_keyboard", "def page_limit_keyboard")
    assert 'callback_data="set_mode"' in ui
    assert "_mode_button(" not in ui
    assert 'text="▶️ Новый скан"' in ui
    assert 'callback_data="set_min_views"' in ui


def test_visible_command_menu_hides_legacy_result_command():
    ui = block("async def setup_bot_commands", "def onboarding_keyboard")
    assert 'command="new_scan"' in ui
    assert 'description="▶️ Новый скан"' in ui
    assert 'command="result"' not in ui


def test_final_scan_card_uses_result_count_and_observation_schedule():
    ui = block("async def finish_job", "async def scan_worker")
    assert "📦 В результате" in ui
    assert "🔔 Автозамеры" in ui
    assert "3 · 6 · 12 ч" in ui
    assert "📄 CSV отправлен ниже" in ui
