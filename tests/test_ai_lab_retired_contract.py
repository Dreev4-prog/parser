from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / 'bot.py').read_text(encoding='utf-8')
LAUNCHER = (ROOT / 'service_launcher.py').read_text(encoding='utf-8')


def test_ai_lab_is_not_in_admin_or_workers_menu():
    admin = BOT.split('def admin_keyboard', 1)[1].split('def admin_back_keyboard', 1)[0]
    workers = BOT.split('def admin_workers_keyboard', 1)[1].split('def admin_active_scans_keyboard', 1)[0]
    assert 'DT AI Lab' not in admin
    assert 'callback_data="adminai"' not in admin
    assert 'DT AI Lab' not in workers


def test_old_ai_callbacks_only_redirect_to_radar3():
    block = BOT.split('@dp.callback_query(F.data == "adminai")', 1)[1].split('@dp.callback_query(F.data == "adminviews")', 1)[0]
    assert '_radar_autoscan_text()' in block
    assert 'admin_radar_autoscan_keyboard' in block
    assert '_admin_ai_dashboard_safe_text' not in block
    assert '_ai_candidate_rows' not in block


def test_existing_railway_ai_service_is_inert():
    assert '"ai-worker": "retired_ai_worker.py"' in LAUNCHER
    retired = (ROOT / 'retired_ai_worker.py').read_text(encoding='utf-8')
    assert 'Legacy DT AI service is retired' in retired
    assert 'ai_worker' not in retired
