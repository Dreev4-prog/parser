from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_stale_expiry_preserves_confirmed_score():
    block = RADAR.split("async def radar_v3_expire_stale_products", 1)[1].split(
        "async def repair_radar_v3_historical_scores_once", 1
    )[0]
    assert 'status="historical"' in block
    assert 'radar_rank=0.0' in block
    assert 'else_=RadarProduct.last_signal_score' in block
    assert 'current_score=0' not in block


def test_pre_42113_zeroed_history_is_repaired_once():
    block = RADAR.split("async def repair_radar_v3_historical_scores_once", 1)[1].split(
        "async def prepare_radar_v3_once", 1
    )[0]
    assert "RADAR_V3_HISTORY_SCORE_REPAIR_SETTING" in RADAR
    assert "pg_advisory_xact_lock" in block
    assert 'RadarProduct.status == "historical"' in block
    assert "RadarProduct.current_score <= 0" in block
    assert "RadarProduct.last_signal_score > 0" in block
    assert "else_=RadarProduct.peak_score" in block
    assert "repair_radar_v3_historical_scores_once()" in BOT


def test_live_category_and_search_feeds_exclude_history():
    listing = RADAR.split("async def list_radar_products", 1)[1].split(
        "async def search_radar_products", 1
    )[0]
    assert listing.count('conditions.append(RadarProduct.status != "historical")') >= 3
    search = RADAR.split("async def search_radar_products", 1)[1].split(
        "async def radar_categories", 1
    )[0]
    assert 'RadarProduct.status != "historical"' in search
    categories = RADAR.split("async def radar_categories", 1)[1].split(
        "async def get_radar_product", 1
    )[0]
    assert 'RadarProduct.status != "historical"' in categories


def test_records_keep_history_and_use_peak_score():
    listing = RADAR.split("async def list_radar_products", 1)[1].split(
        "async def search_radar_products", 1
    )[0]
    alltime = listing.split('elif mode == "alltime":', 1)[1].split(
        'elif mode == "favorites"', 1
    )[0]
    assert 'RadarProduct.status != "historical"' not in alltime
    keyboard = BOT.split("def radar_list_keyboard", 1)[1].split("def radar_groups_keyboard", 1)[0]
    assert 'if mode == "alltime"' in keyboard
    assert 'product.peak_score' in keyboard
    assert '"historical": "🕒"' in BOT
    assert 'История · сигнал устарел' in BOT


def test_preserved_history_cannot_be_resurrected_by_legacy_refresh():
    block = RADAR.split("async def refresh_radar_scores", 1)[1].split("async def radar_stats", 1)[0]
    assert 'str(product.latest_source or "") == "radar3_observed"' in block
    assert 'signal_age_hours > RADAR_V3_MAX_OBSERVATION_HOURS' in block
    assert 'new_status = "historical"' in block
    assert 'new_rank = 0.0' in block
    assert 'legacy 48H' in block
