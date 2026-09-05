from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text()
RADAR = (ROOT / "radar.py").read_text()

def _module_assignments(src: str):
    tree = ast.parse(src)
    names=set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name): names.add(t.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names: names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names: names.add(a.asname or a.name)
    return names

def test_watchdog_globals_are_really_defined_not_just_referenced():
    names=_module_assignments(BOT)
    assert "RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS" in names
    required = {
        "RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS",
        "RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS",
        "RADAR_AUTOSCAN_PARTIAL_BACKOFF_BASE_SECONDS",
        "RADAR_AUTOSCAN_SYSTEM_BACKOFF_BASE_SECONDS",
        "RADAR_AUTOSCAN_MAX_BACKOFF_SECONDS",
        "RADAR_AUTOSCAN_SUCCESS_GAP_SECONDS",
        "RADAR_AUTOSCAN_SAFE_VIEW_CONCURRENCY",
        "RADAR_AUTOSCAN_LAUNCH_WATCHDOG_SECONDS",
    }
    assert required <= names
    assert '"RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS", 480, 120' in BOT
    assert '"RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS", 240, 60' in BOT
    assert "RADAR_AUTOSCAN_SAFE_VIEW_CONCURRENCY = 4" in BOT
    assert "RADAR_AUTOSCAN_LAUNCH_WATCHDOG_SECONDS = 20" in BOT

def test_startup_radar_guard_cannot_delete_tables():
    block=RADAR.split("async def prepare_radar_v3_once() -> bool:",1)[1].split("async def record_autoscan_hot(",1)[0]
    for token in ("delete(RadarProduct)", "delete(RadarObservation)", "delete(RadarSnapshot)", "delete(RadarFavorite)"):
        assert token not in block

def test_release_keeps_42114_startup_guard():
    assert (ROOT / "VERSION").read_text().strip() == "4.22.0"
