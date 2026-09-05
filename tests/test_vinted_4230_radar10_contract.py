from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
LAB = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
WORKER = (ROOT / "vinted_scan_worker.py").read_text(encoding="utf-8")


def test_radar_10_time_contract_is_24h_live_hourly_and_7d_learning():
    assert 'VINTED_RADAR_LIVE_HOURS = max(6, min(48, int(os.getenv("VINTED_RADAR_LIVE_HOURS", "24")' in RADAR
    assert 'VINTED_RADAR_INTERVAL_MINUTES = max(15, min(360, int(os.getenv("VINTED_RADAR_INTERVAL_MINUTES", "60")' in RADAR
    assert 'VINTED_RADAR_HISTORY_DAYS = max(2, min(30, int(os.getenv("VINTED_RADAR_HISTORY_DAYS", "7")' in RADAR


def test_first_observation_can_never_publish_hot_or_rising():
    status_block = RADAR.split('def _status_for(', 1)[1].split('\n\n\nasync def _build_snapshot', 1)[0]
    assert 'movement = sample_count >= 2 and like_delta is not None and like_delta > 0' in status_block
    assert 'if movement and score >= 75:' in status_block
    assert 'if movement and score >= 58:' in status_block
    assert 'if int(p["sample_count"]) < 2:' in RADAR
    assert 'score = min(score, 59)' in RADAR


def test_counter_regression_is_invalid_interval_not_negative_demand():
    rate_block = RADAR.split('def _sample_rate(', 1)[1].split('\n\n\ndef _median', 1)[0]
    assert 'if delta < 0:' in rate_block
    assert 'return None, seconds / 3600.0, None' in rate_block


def test_radar_mode_is_catalog_only_and_does_not_queue_blocked_detail_metrics():
    assert "scan_collects_detail_metrics" in WORKER
    assert "if collect_detail_metrics:" in WORKER
    assert 'str(row.mode or "manual") != "radar"' in LAB
    assert 'radar_catalog_only = str(scan.mode or "manual") == "radar"' in LAB
    assert 'scan.metrics_total = 0 if radar_catalog_only else scan.total_items' in LAB


def test_radar_score_uses_likes_price_peers_and_not_views():
    scoring = RADAR.split('components = {', 1)[1].split('}', 1)[0]
    assert '"like_velocity"' in scoring
    assert '"acceleration"' in scoring
    assert '"price_edge"' in scoring
    assert '"likes_vs_peers"' in scoring
    assert '"scarcity"' in scoring
    assert '"seller"' in scoring
    assert '"brand_momentum"' in scoring
    assert "view_count" not in scoring


def test_learning_pool_is_frozen_at_live_window_end():
    assert "live_cutoff = first_seen + timedelta(hours=VINTED_RADAR_LIVE_HOURS)" in RADAR
    assert "sample.scan_created_at <= live_cutoff" in RADAR
    assert "never inflate the 0-24h reference pool" in RADAR


def test_admin_ui_exposes_live_radar_and_autoscan_controls():
    assert 'text="📡 Vinted Radar 1.0"' in BOT
    assert 'text="⏸ Остановить Radar AutoScan"' in BOT
    assert 'vinted_radar_autoscan_scheduler' in BOT
    assert 'name="vinted-radar-1-autoscan"' in BOT
    assert "Первый замер ❤️ — только baseline" in BOT
