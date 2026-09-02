from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _block():
    return RADAR.split("async def radar_v3_record_refreshed", 1)[1].split("async def radar_v3_expire_observations", 1)[0]


def test_adaptive_gate_constants_are_exact():
    assert "RADAR_V3_NOISE_FLOOR_VPH = 3.0" in RADAR
    assert "RADAR_V3_CANDIDATE_PERCENTILE = 0.90" in RADAR
    assert "RADAR_V3_EARLY_PERCENTILE = 0.95" in RADAR
    assert "RADAR_V3_STRONG_PERCENTILE = 0.98" in RADAR
    assert "RADAR_V3_HOT_PERCENTILE = 0.99" in RADAR


def test_only_category_qualified_rows_can_publish_score():
    block = _block()
    assert 'if str(obs.status) not in {"observed", "confirmed"}:' in block
    assert "continue" in block
    assert 'obs.status, obs.next_check_at = "quiet", None' in block
    assert 'obs.status = "candidate"' in block


def test_strong_is_category_relative_not_global_60_vph():
    block = _block()
    assert 'pct >= RADAR_V3_STRONG_PERCENTILE' in block
    assert 'vph >= thresholds["strong"]' in block
    assert 'demand_status, stage = "rising", "confirmed"' in block
    assert "RADAR_V3_STRONG_VPH" not in RADAR


def test_reset_marker_forces_clean_radar_after_upgrade():
    assert 'dt_radar_v3_observed_demand_reset_v6_radar32_two_pass_clean' in RADAR
    reset = RADAR.split("async def prepare_radar_v3_once", 1)[1].split("async def record_autoscan_hot", 1)[0]
    for table in ("RadarFavorite", "RadarLifecycleWatch", "RadarSnapshot", "RadarProductListing", "RadarProduct", "RadarObservation"):
        assert f"delete({table})" in reset


def test_dashboard_explains_adaptive_gate():
    assert "Candidate · топ-10% категории" in BOT
    assert "Early/Score · топ-5% категории" in BOT
    assert "Strong interval · топ-2% категории" in BOT
    assert "P90 Candidate · P95 Early/Score · P98 Strong · P99 Hot" in BOT
