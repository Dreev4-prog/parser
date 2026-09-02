from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_observation_window_stays_six_hours_but_live_retention_is_24h():
    assert "RADAR_V3_MAX_OBSERVATION_HOURS = 6" in RADAR
    assert "RADAR_V3_LIVE_RETENTION_HOURS = 24" in RADAR
    seed = RADAR.split("async def record_autoscan_hot_detailed", 1)[1].split(
        "async def record_user_scan_radar3_baselines", 1
    )[0]
    assert "timedelta(hours=RADAR_V3_MAX_OBSERVATION_HOURS)" in seed
    snapshot = RADAR.split("def _snapshot_live_evidence", 1)[1].split(
        "def _next_lifecycle_checkpoint", 1
    )[0]
    assert "RADAR_V3_LIVE_RETENTION_HOURS * 60" in snapshot


def test_stale_product_expiry_uses_24h_retention_and_preserves_score():
    block = RADAR.split("async def radar_v3_expire_stale_products", 1)[1].split(
        "async def radar_v3_rollover_successful_category", 1
    )[0]
    assert "RADAR_V3_LIVE_RETENTION_HOURS" in block
    assert 'status="historical"' in block
    assert "else_=RadarProduct.last_signal_score" in block
    assert "current_score=0" not in block


def test_successful_category_rollover_retires_only_absent_live_families():
    block = RADAR.split("async def radar_v3_rollover_successful_category", 1)[1].split(
        "async def repair_radar_v3_historical_scores_once", 1
    )[0]
    assert "RadarObservation.external_id.in_(ids)" in block
    assert "Listing.is_promoted.is_(False)" in block
    assert "Listing.is_price_reduced.is_(False)" in block
    assert "select(RadarObservation.product_key)" in block
    assert "current_product_keys" in block
    assert "RadarProduct.product_key.notin_" in block
    assert 'RadarProduct.latest_source == "radar3_observed"' in block
    assert 'RadarProduct.status != "historical"' in block
    assert 'status="historical"' in block
    assert "radar_rank=0.0" in block


def test_rollover_runs_only_after_successful_autoscan_category():
    runner = BOT.split("async def _run_radar_autoscan_round_inner", 1)[1].split(
        "async def _run_radar_autoscan_round", 1
    )[0]
    success = runner.split(
        "if result is not None and result.date_complete and not failure_kind:", 1
    )[1].split("else:", 1)[0]
    assert "radar_v3_rollover_successful_category" in success
    assert "result.matched_ids or []" in success
    assert "24h hard cap remains the safe fallback" in runner


def test_legacy_refresh_uses_24h_live_boundary_not_observation_ttl():
    block = RADAR.split("async def refresh_radar_scores", 1)[1].split(
        "async def radar_stats", 1
    )[0]
    assert "signal_age_hours > RADAR_V3_LIVE_RETENTION_HOURS" in block
    assert "signal_age_hours > RADAR_V3_MAX_OBSERVATION_HOURS" not in block


def test_first_startup_restores_only_recent_old_ttl_history():
    block = RADAR.split("async def repair_radar_v3_live_retention_once", 1)[1].split(
        "async def prepare_radar_v3_once", 1
    )[0]
    assert "RADAR_V3_LIVE_RETENTION_REPAIR_SETTING" in RADAR
    assert "RadarProduct.last_signal_at >= live_cutoff" in block
    assert "RadarProduct.last_signal_at < old_ttl_cutoff" in block
    assert "RADAR_V3_LIVE_RETENTION_HOURS" in block
    assert "RADAR_V3_MAX_OBSERVATION_HOURS" in block
    assert "RadarSnapshot.source == \"radar3_observed\"" in block
    assert '{"stable", "rising", "hot"}' in block
    assert "repair_radar_v3_live_retention_once()" in BOT
