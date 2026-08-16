import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_all_railway_start_commands_are_pure_executables():
    expected = {
        "railway.json": "python bot.py",
        "railway.bot.json": "python bot.py",
        "railway.fleet-worker.json": "python fleet_worker.py",
        "railway.views-worker.json": "python views_worker.py",
        "railway.parser-worker.json": "python parser_worker.py",
        "railway.browser-worker.json": "python browser_worker.py",
        "railway.hybrid-worker.json": "python hybrid_worker.py",
        "railway.stable-worker.json": "python stable_worker.py",
    }
    for filename, wanted in expected.items():
        cfg = json.loads((ROOT / filename).read_text(encoding="utf-8"))
        command = cfg["deploy"]["startCommand"]
        assert command == wanted
        assert "=" not in command.split()[0]


def test_distributed_scan_checks_for_live_workers_before_enqueue():
    assert 'COORDINATOR.worker_count(prefix="parser")' in BOT
    assert "Browser Fleet не запущен" in BOT
    assert "DISTRIBUTED_WORKER_READY_WAIT_SECONDS" in BOT


def test_version_414():
    assert (ROOT / "VERSION").read_text().strip() == "4.1.5"
    assert 'APP_VERSION = "4.1.5"' in BOT
