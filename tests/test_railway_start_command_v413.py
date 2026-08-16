import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_railway_start_command_is_executable_command_only():
    cfg = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    cmd = cfg["deploy"]["startCommand"]
    assert cmd == "python bot.py"
    assert "PRIMARY_SCAN_INLINE_VIEWS" not in cmd.upper()
    assert "=" not in cmd.split()[0]

def test_inline_views_remains_configuration_not_start_command():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "PRIMARY_SCAN_INLINE_VIEWS" in env_example
