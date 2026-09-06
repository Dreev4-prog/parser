from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
LAB = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
METRICS = (ROOT / "vinted_metrics_worker.py").read_text(encoding="utf-8")
MODELS = (ROOT / "models.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_followup_has_durable_watch_table_and_absolute_schedule():
    assert "class VintedRadarWatch(Base):" in MODELS
    assert '__tablename__ = "vinted_radar_watches"' in MODELS
    assert "VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES = (30, 60, 120, 180)" in LAB
    assert "next_check_at" in MODELS
    assert "lease_until" in MODELS


def test_discovery_is_bounded_and_does_not_replace_public_score():
    assert 'VINTED_RADAR_FOLLOWUP_MIN_DISCOVERY_SCORE", "42"' in RADAR
    assert 'VINTED_RADAR_FOLLOWUP_MAX_NEW_PER_HOUR", "1500"' in RADAR
    assert 'VINTED_RADAR_FOLLOWUP_MAX_ACTIVE", "4500"' in RADAR
    assert "This is intentionally a *discovery* score, not the user-facing Vinted Score 100." in RADAR
    assert "score < VINTED_RADAR_FOLLOWUP_MIN_DISCOVERY_SCORE" in RADAR


def test_autoscan_stop_only_stops_new_discovery_existing_watches_continue():
    seed = RADAR.split("async def seed_followup_candidates", 1)[1].split("async def dispatch_due_followups", 1)[0]
    dispatch = RADAR.split("async def dispatch_due_followups", 1)[1].split("_followup_last_seed_mono", 1)[0]
    assert 'if not cfg.get("enabled"):' in seed
    assert "Stopping AutoScan stops new discovery" in seed
    assert 'cfg.get("enabled")' not in dispatch
    assert "уже выбранные Follow-up продолжаются" in BOT


def test_followup_runs_on_metrics_worker_and_stays_identity_bound():
    assert "enqueue_radar_followup" in LAB
    assert '"purpose": "radar_followup"' in LAB
    assert 'str(fields.get("purpose") or "") == "radar_followup"' in METRICS
    assert "claim_radar_followup_processing" in METRICS
    assert "save_radar_followup_sample" in METRICS
    helper = LAB.split("async def save_radar_followup_sample", 1)[1].split("async def mark_radar_followup_error", 1)[0]
    assert "identity_ok" in helper
    assert "favourite_count" in helper
    assert 'source=(f"radar_followup:{source}"' in helper
    assert "UNKNOWN responses remain fail-closed" in helper


def test_followup_samples_are_merged_and_near_duplicates_coalesced():
    assert 'VintedMetricHistory.source.like("radar_followup%")' in RADAR
    scoring = RADAR.split("def _score_snapshot_rows", 1)[1].split("async def _build_snapshot", 1)[0]
    assert "Targeted follow-up samples are identity-bound favourites" in scoring
    assert "known = _coalesce_like_samples(radar_samples)" in scoring
    coalesce = RADAR.split("def _coalesce_like_samples", 1)[1].split("def _discovery_absolute_like_points", 1)[0]
    assert "min_gap_seconds: int = 600" in coalesce
    assert "result[-1] = sample" in coalesce


def test_followup_maintenance_is_wired_to_radar_scheduler_and_ui():
    assert "maintain_followup_lane as maintain_vinted_radar_followup_lane" in BOT
    scheduler = BOT.split("async def vinted_radar_autoscan_scheduler", 1)[1].split("# ---- end Vinted Lab", 1)[0]
    assert "await maintain_vinted_radar_followup_lane()" in scheduler
    assert "Follow-up Lane" in BOT
    assert "snapshot.followup_active" in BOT
    assert "snapshot.followup_due" in BOT
    assert "snapshot.followup_samples" in BOT


def test_hot_rising_quality_gates_remain_unchanged():
    assert "if movement and score >= 75:" in RADAR
    assert "if movement and score >= 58:" in RADAR
    assert "movement = sample_count >= 2 and like_delta is not None and like_delta > 0" in RADAR
    assert 'VINTED_RADAR_MIN_PRICE_EUR", "40"' in RADAR
