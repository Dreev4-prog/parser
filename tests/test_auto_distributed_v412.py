from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIST = (ROOT / "distributed.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
FLEET = (ROOT / "fleet_worker.py").read_text(encoding="utf-8")

def _probe(extra):
    env = os.environ.copy()
    env.update(extra)
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.check_output(
        [sys.executable, "-c", "import distributed; print(int(distributed.DISTRIBUTED_WORKERS), distributed.DISTRIBUTED_MODE_SOURCE, int(distributed.DISTRIBUTED_CONFIG_ERROR))"],
        cwd=ROOT, env=env, text=True,
    ).strip()
    return out

def test_redis_auto_enables_even_with_stale_zero():
    out = _probe({"REDIS_URL": "redis://example.invalid:6379/0", "DISTRIBUTED_WORKERS": "0", "RAILWAY_PROJECT_ID": "p"})
    assert out == "1 redis-auto 0"

def test_railway_without_redis_is_configuration_error():
    out = _probe({"REDIS_URL": "", "DISTRIBUTED_WORKERS": "0", "RAILWAY_PROJECT_ID": "p", "RAILWAY_REQUIRES_REDIS": "1"})
    assert out == "0 no-redis 1"

def test_force_local_is_explicit_escape_hatch():
    out = _probe({"REDIS_URL": "redis://example.invalid:6379/0", "FORCE_LOCAL_MODE": "1", "RAILWAY_PROJECT_ID": ""})
    assert out == "0 forced-local 0"

def test_bot_distributed_mode_disables_inline_views_and_local_workers():
    assert "if DISTRIBUTED_WORKERS:\n    PRIMARY_SCAN_INLINE_VIEWS = False" in BOT
    assert "worker_tasks = [] if DISTRIBUTED_WORKERS else" in BOT
    assert "local_workers=%s" in BOT

def test_fleet_worker_forces_distributed_role_before_import():
    assert 'os.environ["DISTRIBUTED_WORKERS"] = "1"' in FLEET
    assert FLEET.index('os.environ["DISTRIBUTED_WORKERS"] = "1"') < FLEET.index("from parser_worker import main")
