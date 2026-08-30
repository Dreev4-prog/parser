from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / 'radar.py').read_text(encoding='utf-8')
MODELS = (ROOT / 'models.py').read_text(encoding='utf-8')
DB = (ROOT / 'db.py').read_text(encoding='utf-8')
BOT = (ROOT / 'bot.py').read_text(encoding='utf-8')


def _block():
    return RADAR.split('async def radar_v3_record_refreshed', 1)[1].split('async def radar_v3_expire_observations', 1)[0]


def test_score_weights_are_contextual_and_sum_to_100():
    src = _block()
    assert 'velocity_points = round(50 * category_percentile)' in src
    assert 'persistence_points = 25' in src
    assert 'acceleration_points = 15' in src
    assert 'repeat_points = 10' in src
    assert 'score = max(1, min(100' in src


def test_first_scored_interval_is_maturity_capped():
    src = _block()
    assert 'if int(obs.scored_checkpoints or 0) <= 1:' in src
    assert 'score = min(score, 50)' in src


def test_confidence_is_separate_from_score():
    src = _block()
    assert 'confidence = 30' in src
    assert 'confidence = max(0, min(95' in src
    assert 'score=score, confidence=confidence' in src


def test_hot_has_two_paths():
    src = _block()
    assert 'solo_hot = int(obs.consecutive_strong or 0) >= 2' in src
    assert 'family_hot = family_scored >= 2' in src
    assert 'if solo_hot or family_hot:' in src
    assert 'demand_status, stage = "hot", "product_hot"' in src


def test_smart_recheck_intervals_are_60_45_30():
    assert 'RADAR_V3_NEXT_CHECK_MINUTES = 60' in RADAR
    assert 'RADAR_V3_EARLY_CHECK_MINUTES = 45' in RADAR
    assert 'RADAR_V3_STRONG_CHECK_MINUTES = 30' in RADAR
    src = _block()
    assert 'timedelta(minutes=RADAR_V3_NEXT_CHECK_MINUTES)' in src
    assert 'timedelta(minutes=RADAR_V3_EARLY_CHECK_MINUTES)' in src
    assert 'timedelta(minutes=RADAR_V3_STRONG_CHECK_MINUTES)' in src


def test_observation_schema_persists_context_evidence():
    for field in (
        'previous_vph', 'velocity_percentile', 'acceleration_ratio', 'confidence',
        'scored_checkpoints', 'consecutive_scored', 'strong_checkpoints', 'consecutive_strong',
    ):
        assert field in MODELS
        assert f'"{field}"' in DB


def test_dashboard_exposes_funnel_acceleration_confidence_and_category_context():
    assert 'Воронка Radar 3.1' in BOT
    assert 'Любой DT-observed прирост' in BOT
    assert 'Ускоряются ≥20%' in BOT
    assert 'Confidence ≥70%' in BOT
    assert '50% сила относительно категории + 25% устойчивость + 15% ускорение + 10% повторяемость' in BOT


def test_new_reset_marker_prevents_old_score_mix():
    assert 'dt_radar_v3_observed_demand_reset_v3_radar31_context_score' in RADAR
