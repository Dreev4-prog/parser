from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
RADAR=(ROOT/"radar.py").read_text()
BOT=(ROOT/"bot.py").read_text()

def test_autoscan_excludes_non_product_priority_groups():
    block=RADAR[RADAR.index("RADAR_V3_EXCLUDED_GROUPS"):RADAR.index("RADAR_V3_EXCLUDED_CATEGORY_KEYS")]
    for key in ("auto","immobilien","jobs","services","kurse","hilfe"):
        assert f'"{key}"' in block
    helper=BOT.split("def _radar_autoscan_category_allowed",1)[1].split("def _radar_autoscan_categories",1)[0]
    assert "radar_v3_category_allowed" in helper

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
