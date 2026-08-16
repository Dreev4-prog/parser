from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = (ROOT / "distributed.py").read_text(encoding="utf-8")
WORKER = (ROOT / "hybrid_worker.py").read_text(encoding="utf-8")
ENV = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_v371_version():
    assert (ROOT / "VERSION").read_text().strip() == "4.1.6"
    assert 'APP_VERSION = "4.1.6"' in (ROOT / "bot.py").read_text()


def test_distributed_spacing_is_per_replica_not_one_global_clock():
    assert 'RAILWAY_REPLICA_ID' in DIST
    assert 'traffic:next:{kind}:{self._traffic_lane_id()}' in DIST
    assert 'traffic:active:global' in DIST


def test_hybrid_profile_forces_five_foreground_lanes():
    assert 'HYBRID_SCAN_LANES' in WORKER
    assert 'DIST_TRAFFIC_SCAN_LIMIT' in WORKER
    assert 'HYBRID_GLOBAL_LANES' in WORKER
    assert 'DIST_TRAFFIC_GLOBAL_LIMIT' in WORKER
    assert 'HYBRID_SCAN_LANES=5' in ENV
