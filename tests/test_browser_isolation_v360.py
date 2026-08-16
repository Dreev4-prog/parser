from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSER = (ROOT / "parser.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
BROWSER_WORKER = (ROOT / "browser_worker.py").read_text(encoding="utf-8")
DIST = (ROOT / "distributed.py").read_text(encoding="utf-8")
TRAFFIC = (ROOT / "traffic.py").read_text(encoding="utf-8")


def test_browser_worker_forces_one_isolated_browser_lane():
    assert 'SCAN_TRANSPORT", "browser"' in BROWSER_WORKER
    assert 'PARSER_WORKER_CONCURRENCY", "1"' in BROWSER_WORKER
    assert 'SHARE_ACTIVE_CATEGORY_SCANS", "0"' in BROWSER_WORKER
    assert 'DIST_TRAFFIC_SHARED_COOLDOWN", "0"' in BROWSER_WORKER


def test_foreground_category_pages_have_real_chromium_transport():
    assert "_fetch_scan_browser_document" in PARSER
    assert 'page.goto(' in PARSER
    assert 'wait_until="domcontentloaded"' in PARSER
    assert 'traffic_kind == "scan" and self.scan_transport == "browser"' in PARSER


def test_one_parser_session_is_reused_for_whole_user_job():
    assert "JOB_PARSER: ContextVar" in BOT
    assert "parser_token = JOB_PARSER.set(parser)" in BOT
    assert "parser = JOB_PARSER.get()" in BOT
    assert "await parser.close()" in BOT


def test_active_browser_scans_are_not_forced_to_share_one_owner():
    assert "if not SHARE_ACTIVE_CATEGORY_SCANS:" in BOT
    assert "category-scan-isolated" in BOT


def test_browser_refusal_does_not_publish_global_freeze():
    assert "DIST_TRAFFIC_SHARED_COOLDOWN" in DIST
    assert "DIST_TRAFFIC_SHARED_COOLDOWN" in TRAFFIC
    assert "DISTRIBUTED_WORKERS and DIST_TRAFFIC_SHARED_COOLDOWN" in TRAFFIC


def test_browser_worker_has_railway_start_config():
    cfg = (ROOT / "railway.browser-worker.json").read_text(encoding="utf-8")
    assert 'python browser_worker.py' in cfg
