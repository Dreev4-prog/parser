from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_embedded_fleet_fallback_defaults_on():
    assert 'EMBEDDED_FLEET_FALLBACK = os.getenv("EMBEDDED_FLEET_FALLBACK", "1")' in BOT


def test_embedded_fleet_uses_browser_transport_and_heartbeat():
    assert 'parser_module.SCAN_TRANSPORT = "browser"' in BOT
    assert 'parser_module.SHARED_BROWSER_RUNTIME = True' in BOT
    assert 'distributed_worker_heartbeat(worker_id, "parser")' in BOT
    assert 'distributed_scan_worker(bot, f"{worker_id}-1")' in BOT


def test_stale_external_heartbeat_cannot_suppress_embedded_bootstrap():
    assert 'External Browser Fleet detected at startup' not in BOT
    assert 'if external_workers > 0:' not in BOT
    assert 'await COORDINATOR.heartbeat(worker_id, "parser")' in BOT
    assert 'Embedded Browser Fleet reserve online' in BOT


def test_start_command_remains_clean():
    import json
    cfg = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    assert cfg["deploy"]["startCommand"] == "python bot.py"
