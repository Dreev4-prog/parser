from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_gate_constants_are_exact():
    assert "RADAR_V3_CANDIDATE_VPH = 15.0" in RADAR
    assert "RADAR_V3_SCORE_VPH = 30.0" in RADAR
    assert "RADAR_V3_STRONG_VPH = 60.0" in RADAR


def test_below_score_gate_never_publishes_signal():
    block = RADAR.split("async def radar_v3_record_refreshed", 1)[1].split("async def radar_v3_expire_observations", 1)[0]
    assert "if vph < RADAR_V3_SCORE_VPH:" in block
    assert "continue" in block.split("if vph < RADAR_V3_SCORE_VPH:", 1)[1].split("async with SessionLocal()", 1)[0]
    assert 'obs.status = "candidate"' in block
    assert 'obs.status = "quiet"' in block


def test_60_per_hour_is_strong_on_first_scored_interval():
    block = RADAR.split("async def radar_v3_record_refreshed", 1)[1].split("async def radar_v3_expire_observations", 1)[0]
    assert 'float(vph) >= RADAR_V3_STRONG_VPH' in block
    assert 'demand_status, stage = "rising", "confirmed"' in block


def test_reset_marker_forces_clean_radar_after_upgrade():
    assert 'dt_radar_v3_observed_demand_reset_v3_radar31_context_score' in RADAR
    reset = RADAR.split("async def prepare_radar_v3_once", 1)[1].split("async def record_autoscan_hot", 1)[0]
    for table in ("RadarFavorite", "RadarLifecycleWatch", "RadarSnapshot", "RadarProductListing", "RadarProduct", "RadarObservation"):
        assert f"delete({table})" in reset


def test_dashboard_explains_gate():
    assert "Candidate 15–29/ч, без Score" in BOT
    assert "Score Gate ≥30/ч" in BOT
    assert "≥60/ч — Strong" in BOT
