from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "fleet_worker.py").read_text(encoding="utf-8")
PARSER = (ROOT / "parser.py").read_text(encoding="utf-8")
PARSER_WORKER = (ROOT / "parser_worker.py").read_text(encoding="utf-8")


def test_v400_version_and_entrypoint():
    assert (ROOT / "VERSION").read_text().strip() == "4.0.4"
    assert 'APP_VERSION = "4.0.4"' in (ROOT / "bot.py").read_text(encoding="utf-8")
    cfg = (ROOT / "railway.fleet-worker.json").read_text(encoding="utf-8")
    assert "python fleet_worker.py" in cfg


def test_fleet_worker_uses_shared_chromium_and_isolated_contexts():
    assert 'FLEET_CONTEXTS_PER_REPLICA' in WORKER
    assert 'os.environ["SCAN_TRANSPORT"] = "browser"' in WORKER
    assert 'os.environ["SHARED_BROWSER_RUNTIME"] = "1"' in WORKER
    assert 'os.environ["SHARE_ACTIVE_CATEGORY_SCANS"] = "0"' in WORKER
    assert 'os.environ["PARSER_WORKER_CONCURRENCY"] = str(LOCAL_CONTEXTS)' in WORKER


def test_parser_has_one_process_local_browser_runtime():
    assert 'class _SharedBrowserRuntime' in PARSER
    assert 'Railway browser fleet runtime started' in PARSER
    assert 'await browser.new_context(' in PARSER
    assert 'if SHARED_BROWSER_RUNTIME:' in PARSER
    assert 'await _SHARED_BROWSER_FLEET.new_context(' in PARSER


def test_shared_runtime_is_shutdown_by_worker():
    assert 'shutdown_shared_browser_runtime' in PARSER_WORKER
    assert 'await shutdown_shared_browser_runtime()' in PARSER_WORKER


def test_fleet_keeps_global_governor_enabled():
    assert 'DIST_TRAFFIC_SHARED_COOLDOWN"] = "1"' in WORKER
    assert 'FLEET_TOTAL_SCAN_LANES' in WORKER
    assert 'FLEET_TOTAL_GLOBAL_LANES' in WORKER
