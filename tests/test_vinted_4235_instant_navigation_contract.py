from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _block(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def test_vinted_worker_and_progress_reads_have_short_ui_cache():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "VINTED_UI_STATUS_CACHE_SECONDS = 2.0" in bot
    assert "VINTED_UI_PROGRESS_CACHE_SECONDS = 3.0" in bot
    worker = _block(bot, "async def _vinted_worker_status_fast", "async def _vinted_list_scans_fast")
    assert "_VINTED_WORKER_STATUS_CACHE" in worker
    assert "Stale-while-revalidate" in worker
    assert "timeout: float = 0.75" in worker
    progress = _block(bot, "async def _vinted_scan_progress_fast", "def _vinted_home_keyboard")
    assert "_VINTED_SCAN_PROGRESS_CACHE" in progress
    assert "asyncio.wait_for(vinted_scan_progress" in progress


def test_scan_and_radar_screens_use_cached_progress_path():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    scan = _block(bot, "async def _vinted_scan_text", "async def _vinted_workers_text")
    radar = _block(bot, "async def _vinted_radar_screen", "async def _vinted_radar_item_screen")
    assert "_vinted_scan_progress_fast(scan_id)" in scan
    assert "_vinted_scan_progress_fast(int(snapshot.last_scan_id), wait_if_cold=False)" in radar
    assert "await get_vinted_scan" not in radar


def test_category_tree_is_flattened_once_per_long_ui_cache_window():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = _block(bot, "async def _vinted_tree_context", "def _vinted_setup_header")
    assert "VINTED_UI_TREE_CACHE_SECONDS = 1800.0" in bot
    assert "_VINTED_TREE_CONTEXT_CACHE" in tree
    assert "flat = flatten_vinted_catalog_tree(roots)" in tree


def test_radar_item_click_never_rebuilds_stale_snapshot_synchronously():
    radar = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
    entry = radar[radar.index("async def get_radar_entry"):]
    assert "if force:" in entry
    assert "request_radar_snapshot_refresh(force=False)" in entry
    assert "return _cache_index.get(target)" in entry
    assert "snapshot = await build_radar_snapshot(force=force)" not in entry
    assert "_cache_index = {int(entry.item_id): entry for entry in snapshot.entries}" in radar


def test_radar_config_has_short_memory_cache_for_ui_reads():
    radar = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
    cfg = _block(radar, "async def radar_config", "async def resolve_all_market_categories")
    assert "VINTED_RADAR_UI_CONFIG_CACHE_SECONDS = 3.0" in radar
    assert "_config_cache" in cfg
    assert "_config_cache = (time.monotonic(), cfg)" in cfg


def test_radar_results_do_not_count_and_sort_full_scan_table_each_click():
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    items = _block(lab, "async def list_scan_items", "async def get_scan_item")
    assert 'radar_mode = str(scan.mode or "manual") == "radar"' in items
    assert "total = int(scan.total_items or 0)" in items
    assert "order_by = (VintedScanItem.id.asc(),)" in items
