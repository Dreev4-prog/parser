from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
LAB = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
RADAR = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")


def test_start_button_renders_progress_before_catalog_network_work():
    block = BOT.split('if action == "radarstart":', 1)[1].split('if action == "radarstop":', 1)[0]
    assert '1/3' in block
    assert block.index('1/3') < block.index('resolve_vinted_radar_categories(force=False)')
    assert '2/3' in block and '3/3' in block


def test_start_errors_are_rendered_not_second_callback_answers():
    block = BOT.split('if action == "radarstart":', 1)[1].split('if action == "radarstop":', 1)[0]
    assert 'callback.answer(' not in block
    assert 'Повторить запуск' in block
    assert 'Частичный Radar специально не запускаю' in block


def test_full_market_has_public_metadata_snapshot_fallback():
    assert 'VINTED_CATALOG_SNAPSHOT_URL' in LAB
    assert 'raw.githubusercontent.com/JakobAIOdev/vinted-dataset' in LAB
    assert 'async def _fetch_catalog_snapshot()' in LAB
    assert 'def _normalize_catalog_snapshot(' in LAB
    assert '"snapshot-de"' in LAB
    assert 'startswith(("live", "snapshot"))' in RADAR


def test_forced_refresh_keeps_last_valid_complete_tree():
    assert 'previous_cache = _catalog_cache' in LAB
    assert 'startswith(("live", "snapshot"))' in LAB
