from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _block(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def test_vinted_home_does_not_build_heavy_snapshot_or_scan_item_aggregate():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    home = _block(bot, "async def _vinted_home_text()", "async def _vinted_tree_context()")
    assert "build_vinted_radar_snapshot()" not in home
    assert "vinted_scan_progress(" not in home
    assert "vinted_radar_overview()" in home


def test_radar_ui_uses_cached_snapshot_and_background_singleflight():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    screen = _block(bot, "async def _vinted_radar_screen", "async def _vinted_radar_item_screen")
    assert "peek_vinted_radar_snapshot()" in screen
    assert "request_vinted_radar_snapshot_refresh(force=False)" in screen
    assert "await build_vinted_radar_snapshot()" not in screen
    assert "пересчитывается в фоне" in screen


def test_radar_scoring_is_off_event_loop_and_cache_is_longer():
    radar = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
    assert 'os.getenv("VINTED_RADAR_CACHE_SECONDS", "120")' in radar
    assert "await asyncio.to_thread(_score_snapshot_rows, rows, followup_rows, now)" in radar
    assert "request_radar_snapshot_refresh" in radar
    assert "_refresh_task" in radar
    assert "select(\n                VintedScanItem.id," in radar
    assert "select(VintedScanItem, VintedScan.created_at)" not in radar


def test_radar_progress_skips_full_like_aggregate():
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    progress = _block(lab, "async def scan_progress_snapshot", "async def catalog_like_delta")
    assert 'if str(scan.mode or "manual") == "radar":' in progress
    assert '"deferred": True' in progress
    assert "SUM/MAX/COUNT" in progress


def test_radar_recalc_does_not_load_every_item_and_page_counter_is_atomic():
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    recalc = _block(lab, "async def recalc_scan", "async def mark_category_running")
    assert "radar_catalog_only" in recalc
    assert "metric_rows = list" not in recalc
    save = _block(lab, "async def save_catalog_page", "async def complete_category")
    assert "func.coalesce(VintedScan.total_items, 0) + len(new_ids)" in save
    assert "synchronize_session=False" in save


def test_scan_watcher_is_message_scoped_and_cancelled_on_navigation():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_VINTED_ADMIN_WATCHERS: dict[tuple[int, int], asyncio.Task]" in bot
    assert "await asyncio.sleep(8)" in bot
    assert "await _vinted_cancel_message_watcher(callback)" in bot
