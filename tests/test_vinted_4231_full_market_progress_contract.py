from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
LAB = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
WORKER = (ROOT / "vinted_scan_worker.py").read_text(encoding="utf-8")


def test_radar_has_fixed_equal_15_page_primary_depth():
    assert 'VINTED_RADAR_PAGES_PER_CATEGORY = 15' in RADAR
    assert '"pages": VINTED_RADAR_PAGES_PER_CATEGORY' in RADAR
    assert 'pages=VINTED_RADAR_PAGES_PER_CATEGORY' in BOT
    assert 'pages_target = max(1, min(15, int(fields.get("pages") or 3)))' in WORKER


def test_radar_scans_leaf_categories_only_to_avoid_parent_child_bias():
    assert 'def leaf_catalogs_from_tree(' in LAB
    block = LAB.split('def leaf_catalogs_from_tree(', 1)[1].split('\n\n\nclass VintedQueueUnavailable', 1)[0]
    assert 'if children:' in block
    assert 'walk(child, current_path)' in block
    assert 'leaves.append' in block
    assert 'VINTED_RADAR_SCOPE = "all_leaf_categories"' in RADAR
    assert 'resolve_all_market_categories' in RADAR


def test_incomplete_fallback_tree_never_silently_claims_full_market():
    assert 'VINTED_RADAR_MIN_LEAF_CATEGORIES = 20' in RADAR
    assert 'if len(leaves) >= VINTED_RADAR_MIN_LEAF_CATEGORIES:' in RADAR
    assert 'cached-full-market' in RADAR
    assert 'return [], str(source or "unavailable")' in RADAR


def test_category_order_rotates_every_round():
    block = RADAR.split('def _rotated_categories(', 1)[1].split('\n\n\nasync def enable_radar', 1)[0]
    assert 'offset = max(0, int(round_index or 0)) % len(categories)' in block
    assert 'return categories[offset:] + categories[:offset]' in block
    assert '_rotated_categories(categories, int(cfg.get("rounds_started") or 0))' in RADAR


def test_radar_never_uses_hidden_recovery_pages_beyond_15():
    assert 'radar_mode = not collect_detail_metrics' in WORKER
    assert 'max_pages = pages_target if radar_mode else pages_target + client.config.recovery_pages' in WORKER
    assert 'if radar_mode and page >= pages_target:' in WORKER
    assert 'if not radar_mode and fetched_pages >= max_pages' in WORKER


def test_progress_snapshot_exposes_real_page_passage():
    assert '"plan_max": page_plan_max' in LAB
    assert '"primary_done": page_primary_done' in LAB
    assert '"fetched_total": page_fetched_total' in LAB
    assert '"category_status": status_counts' in LAB
    assert '📄 Реально пройдено страниц' in BOT
    assert 'Последние / активные категории' in BOT
    assert 'Смотреть проход страниц' in BOT


def test_radar_admin_no_longer_requires_category_selection():
    home = BOT.split('def _vinted_home_keyboard()', 1)[1].split('\n\n\nasync def _vinted_home_text', 1)[0]
    assert 'Настроить Radar' not in home
    assert '▶️ Запустить Radar · весь Vinted' in BOT
    assert 'callback_data="av:radarstart"' in BOT
    radar_start = BOT.split('if action == "radarstart":', 1)[1].split('if action == "radarstop":', 1)[0]
    assert 'resolve_vinted_radar_categories(force=True)' in radar_start
    assert 'categories=categories' in radar_start


def test_full_market_radar_has_bounded_database_retention():
    assert 'async def cleanup_expired_radar_scans(' in RADAR
    assert 'VINTED_RADAR_HISTORY_DAYS + 1' in RADAR
    assert 'delete(VintedScanItem)' in RADAR
    assert 'delete(VintedScanCategory)' in RADAR
    assert 'delete(VintedScan)' in RADAR
    assert 'await cleanup_expired_radar_scans(max_scans=4)' in RADAR
