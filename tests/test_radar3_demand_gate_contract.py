from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _block():
    return RADAR.split("async def radar_v3_record_refreshed", 1)[1].split("async def radar_v3_expire_observations", 1)[0]


def test_old_global_15_30_60_gate_is_retired():
    assert "RADAR_V3_CANDIDATE_VPH =" not in RADAR
    assert "RADAR_V3_SCORE_VPH =" not in RADAR
    assert "RADAR_V3_STRONG_VPH =" not in RADAR
    assert "RADAR_V3_NOISE_FLOOR_VPH = 3.0" in RADAR


def test_adaptive_category_gates_are_authoritative_after_bootstrap():
    block = _block()
    assert 'candidate = positive and vph >= float(thresholds["candidate"])' in block
    assert 'early = positive and vph >= float(thresholds["early"])' in block
    assert 'strong = positive and vph >= float(thresholds["strong"])' in block
    assert 'hot_interval = positive and vph >= float(thresholds["hot"])' in block
    assert 'if str(obs.status or "") not in {"observed", "confirmed"}:' in block


def test_bootstrap_is_only_for_small_category_cohorts():
    block = _block()
    assert 'mature_cohort = int(thresholds.get("peer_count") or 0) >= RADAR_V3_MIN_CATEGORY_PEERS' in block
    assert 'if mature_cohort:' in block
    assert '"candidate": max(RADAR_V3_NOISE_FLOOR_VPH, 8.0)' in RADAR
    assert '"early": max(RADAR_V3_NOISE_FLOOR_VPH, 15.0)' in RADAR
    assert '"strong": max(RADAR_V3_NOISE_FLOOR_VPH, 30.0)' in RADAR


def test_reset_marker_forces_clean_radar_after_upgrade():
    assert 'dt_radar_v3_observed_demand_reset_v6_radar32_frozen_cohort' in RADAR
    reset = RADAR.split("async def prepare_radar_v3_once", 1)[1].split("async def record_autoscan_hot", 1)[0]
    for table in ("RadarFavorite", "RadarLifecycleWatch", "RadarSnapshot", "RadarProductListing", "RadarProduct", "RadarObservation"):
        assert f"delete({table})" in reset


def test_dashboard_explains_adaptive_gate():
    assert "Candidate · топ-10% категории" in BOT
    assert "Early/Score · топ-5% категории" in BOT
    assert "Strong interval · топ-2% категории" in BOT
    assert "P90 Candidate · P95 Early/Score · P98 Strong · P99 Hot" in BOT
