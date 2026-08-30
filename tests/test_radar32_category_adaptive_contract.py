from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
RADAR=(ROOT/"radar.py").read_text()
BOT=(ROOT/"bot.py").read_text()
CATEGORIES=(ROOT/"categories.py").read_text()

def test_all_radar_ingestion_excludes_non_product_priority_groups():
    for key in ("auto","immobilien","jobs","services","kurse","hilfe"):
        assert f'"{key}"' in CATEGORIES
    assert "radar_category_allowed" in BOT
    assert "radar_allowed_category_keys" in RADAR
    assert "Listing.category_key.in_(allowed_category_keys)" in RADAR

def test_category_adaptive_thresholds_exist():
    assert "RADAR_V3_NOISE_FLOOR_VPH = 3.0" in RADAR
    assert "RADAR_V3_CANDIDATE_PERCENTILE = 0.90" in RADAR
    assert "RADAR_V3_EARLY_PERCENTILE = 0.95" in RADAR
    assert "RADAR_V3_STRONG_PERCENTILE = 0.98" in RADAR
    assert "RADAR_V3_HOT_PERCENTILE = 0.99" in RADAR
    assert "_radar32_thresholds" in RADAR

def test_old_absolute_gate_is_not_runtime_authority():
    assert "RADAR_V3_CANDIDATE_VPH =" not in RADAR
    assert "RADAR_V3_SCORE_VPH =" not in RADAR
    assert "RADAR_V3_STRONG_VPH =" not in RADAR

def test_analytics_explains_adaptive_model():
    assert "P90 Candidate" in BOT
    assert "P95 Early/Score" in BOT
    assert "P98 Strong" in BOT
    assert "P99 Hot" in BOT


def test_frozen_two_pass_category_context_prevents_order_bias():
    block=RADAR.split("async def radar_v3_record_refreshed",1)[1].split("async def radar_v3_expire_observations",1)[0]
    assert "PASS 1" in block and "PASS 2" in block
    assert "category_cohorts" in block and "category_context" in block
    assert "~RadarObservation.external_id.in_(refreshed_ids)" in block
    assert "for row in prepared:" in block

def test_category_distribution_keeps_quiet_zero_growth_rows():
    helper=RADAR.split("def _radar32_thresholds",1)[1].split("async def radar_v3_release_claims",1)[0]
    assert "float(x) >= 0.0" in helper
    assert "float(x) >= RADAR_V3_NOISE_FLOOR_VPH" not in helper

def test_quantile_ties_do_not_require_second_percentile_gate():
    block=RADAR.split("async def radar_v3_record_refreshed",1)[1].split("async def radar_v3_expire_observations",1)[0]
    assert 'candidate = positive and vph >= float(thresholds["candidate"])' in block
    assert 'and category_percentile >= RADAR_V3_CANDIDATE_PERCENTILE' not in block

def test_stale_product_is_retired_when_no_active_early_evidence_remains():
    block=RADAR.split("async def radar_v3_record_refreshed",1)[1].split("async def radar_v3_expire_observations",1)[0]
    assert "affected_product_keys" in block
    assert "retired_keys" in block
    assert 'status="historical", current_score=0, radar_rank=0.0' in block
