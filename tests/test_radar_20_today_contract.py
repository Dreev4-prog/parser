from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / 'bot.py').read_text(encoding='utf-8')
RADAR = (ROOT / 'radar.py').read_text(encoding='utf-8')


def test_autoscan_is_20_pages_today_only():
    assert 'RADAR_AUTOSCAN_DEPTH = 20' in BOT
    assert 'RADAR_CONTEXT_ENABLED = False' in BOT
    assert 'RADAR_CONTEXT_DEPTH = 0' in BOT
    assert 'страниц только за сегодня на категорию' in BOT


def test_old_context_round_cannot_be_scheduled():
    scheduler = BOT.split('async def radar_autoscan_scheduler', 1)[1].split('async def send_smart_export', 1)[0]
    assert '_radar_autoscan_new_context_round' not in scheduler
    assert 'этап 2/2' not in scheduler
    assert '15 страниц за вчера' not in scheduler


def test_old_active_policy_round_is_not_resumed_after_upgrade():
    normalize = BOT.split('def _radar_autoscan_normalize_state', 1)[1].split('async def load_radar_autoscan_state', 1)[0]
    assert 'stored_policy < RADAR_AUTOSCAN_POLICY_VERSION' in normalize
    assert 'state["status"] = "idle"' in normalize
    assert 'state["layer"] = "fresh"' in normalize


def test_user_scans_cannot_seed_shared_radar_baselines():
    assert 'record_user_scan_radar3_baselines' not in BOT
    block = RADAR.split('async def record_user_scan_radar3_baselines', 1)[1].split(
        'async def repair_radar_v3_quality_once', 1
    )[0]
    assert 'return 0' in block
    assert 'SessionLocal' not in block
    assert 'RadarObservation(' not in block
    assert '_radar_reset_observation_cycle' not in block


def test_autoscan_remains_the_unrestricted_radar_baseline_source():
    block = RADAR.split('async def record_autoscan_hot_detailed', 1)[1].split(
        'async def record_user_scan_radar3_baselines', 1
    )[0]
    assert 'new_obs = RadarObservation(' in block
    assert 'baseline_views=raw' in block
    assert 'RADAR_V3_USER_SCAN' not in block
