from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / 'bot.py').read_text(encoding='utf-8')
RADAR = (ROOT / 'radar.py').read_text(encoding='utf-8')
DB = (ROOT / 'db.py').read_text(encoding='utf-8')


def test_dashboard_uses_real_radar_product_status_column():
    block = BOT.split('async def _radar3_dashboard_snapshot', 1)[1].split('async def _radar3_dashboard_safe_snapshot', 1)[0]
    assert 'RadarProduct.demand_status' not in block
    assert 'RadarProduct.status == "stable"' in block
    assert 'RadarProduct.status == "rising"' in block
    assert 'RadarProduct.status == "hot"' in block


def test_radar_panel_acknowledges_before_lightweight_live_state():
    block = BOT.split('@dp.callback_query(F.data == "adminradarauto")', 1)[1].split('@dp.callback_query(F.data == "adminradarauto:analytics")', 1)[0]
    assert block.index('await callback.answer()') < block.index('_radar_autoscan_text()')
    assert 'asyncio.wait_for(_radar_autoscan_text(), timeout=2.0)' in block
    assert '_radar3_dashboard_safe_snapshot' not in block


def test_expiry_update_is_not_nested_inside_where():
    block = RADAR.split('async def radar_v3_expire_observations()', 1)[1].split('async def radar_v3_expire_stale_products', 1)[0]
    assert '.where(\n                update(RadarObservation)' not in block
    assert 'RadarObservation.expires_at <= now' in block
    assert '.values(' in block


def test_rearm_clears_all_context_score_state():
    block = RADAR.split('async def record_autoscan_hot_detailed', 1)[1].split('async def record_user_scan_radar3_baselines', 1)[0]
    for token in (
        'existing.previous_vph = 0.0', 'existing.velocity_percentile = 0.0',
        'existing.acceleration_ratio = 0.0', 'existing.confidence = 0',
        'existing.scored_checkpoints = 0', 'existing.consecutive_scored = 0',
        'existing.strong_checkpoints = 0', 'existing.consecutive_strong = 0',
    ):
        assert token in block


def test_context_cohort_and_family_exclude_expired_or_excluded_rows():
    block = RADAR.split('async def radar_v3_record_refreshed', 1)[1].split('async def radar_v3_expire_observations', 1)[0]
    assert block.count('or_(RadarObservation.expires_at.is_(None), RadarObservation.expires_at > now)') >= 2
    # Category cohort intentionally keeps quiet measured rows to avoid survivor bias.
    assert 'RadarObservation.status.notin_(["expired", "excluded"])' in block
    # Product-family confirmation remains restricted to live scored evidence.
    assert 'RadarObservation.status.in_(["observed", "confirmed"])' in block


def test_stale_radar31_snapshots_cannot_resurrect_hot():
    block = RADAR.split('def _snapshot_live_evidence', 1)[1].split('def _next_lifecycle_checkpoint', 1)[0]
    assert 'RADAR_V3_MAX_OBSERVATION_HOURS * 60' in block
    assert 'RadarRankEvidence("historical", 0.0' in block
    assert 'False)' in block


def test_postgres_indexes_cover_additive_radar31_columns():
    for name in (
        'ix_radar_observations_velocity_percentile',
        'ix_radar_observations_confidence',
        'ix_radar_observations_scored_checkpoints',
        'ix_radar_observations_consecutive_scored',
        'ix_radar_observations_strong_checkpoints',
        'ix_radar_observations_consecutive_strong',
        'ix_radar_observations_acceleration_ratio',
    ):
        assert name in DB
