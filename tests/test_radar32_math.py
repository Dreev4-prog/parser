import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "radar.py").read_text(encoding="utf-8"))
WANTED_CONSTS = {
    "RADAR_V3_NOISE_FLOOR_VPH",
    "RADAR_V3_MIN_CATEGORY_PEERS",
    "RADAR_V3_CANDIDATE_PERCENTILE",
    "RADAR_V3_EARLY_PERCENTILE",
    "RADAR_V3_STRONG_PERCENTILE",
    "RADAR_V3_HOT_PERCENTILE",
}
selected = []
for node in TREE.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in WANTED_CONSTS for t in node.targets):
        selected.append(node)
    if isinstance(node, ast.FunctionDef) and node.name in {"_percentile_rank", "_radar32_thresholds"}:
        selected.append(node)
ns = {}
exec(compile(ast.Module(body=selected, type_ignores=[]), "radar_math", "exec"), ns)
_percentile_rank = ns["_percentile_rank"]
_radar32_thresholds = ns["_radar32_thresholds"]
MIN_PEERS = ns["RADAR_V3_MIN_CATEGORY_PEERS"]


def test_full_category_distribution_including_zeros_drives_thresholds():
    cohort = [0.0] * 18 + [10.0, 20.0]
    assert len(cohort) == MIN_PEERS
    gates = _radar32_thresholds(cohort)
    assert gates["peer_count"] == 20
    assert gates["candidate"] == 3.0
    assert gates["early"] == 10.0
    assert gates["strong"] == 20.0


def test_small_cohort_uses_conservative_bootstrap():
    gates = _radar32_thresholds([0.0, 5.0, 100.0])
    assert gates["peer_count"] == 3
    assert gates["candidate"] == 8.0
    assert gates["early"] == 15.0
    assert gates["strong"] == 30.0
    assert gates["hot"] == 60.0


def test_percentile_rank_is_tie_aware_and_bounded():
    assert 0.0 <= _percentile_rank(10.0, [0.0, 10.0, 10.0, 20.0]) <= 1.0
    assert _percentile_rank(20.0, [0.0, 10.0, 20.0]) > _percentile_rank(10.0, [0.0, 10.0, 20.0])
