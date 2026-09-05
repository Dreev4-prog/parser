from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
RADAR = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
PROBE = (ROOT / "vinted_probe.py").read_text(encoding="utf-8")


def test_balanced_market_plan_targets_about_120_and_hard_caps_150():
    assert 'VINTED_RADAR_TARGET_SEGMENTS = 120' in RADAR
    assert 'VINTED_RADAR_MAX_SEGMENTS = 150' in RADAR
    assert 'target_segments=VINTED_RADAR_TARGET_SEGMENTS' in RADAR
    assert 'max_segments=VINTED_RADAR_MAX_SEGMENTS' in RADAR
    assert 'projected > max_segments' in LAB


def test_segment_partition_replaces_parent_with_children_not_parent_plus_children():
    block = LAB.split('def balanced_catalog_segments_from_tree(', 1)[1].split('\n\n\nclass VintedQueueUnavailable', 1)[0]
    assert 'frontier[idx:idx + 1] = list(chosen.get("children") or [])' in block
    assert 'largest remaining market subtree first' in block
    assert 'every terminal category remains covered' in block


def test_resolver_validates_full_leaf_tree_but_scans_balanced_segments():
    block = RADAR.split('async def resolve_all_market_categories(', 1)[1].split('\n\n\ndef _rotated_categories', 1)[0]
    assert 'leaves = leaf_catalogs_from_tree(roots)' in block
    assert 'len(leaves) >= VINTED_RADAR_MIN_LEAF_CATEGORIES' in block
    assert 'segments = balanced_catalog_segments_from_tree(' in block
    assert 'return segments, plan_source[:80]' in block
    assert 'pages_max' in block


def test_legacy_leaf_scope_is_migrated_without_waiting_an_hour():
    assert 'VINTED_RADAR_SCOPE = "balanced_market_segments_v1"' in RADAR
    block = RADAR.split('async def maybe_start_due_round()', 1)[1].split('\n\n\nasync def next_due_at', 1)[0]
    assert 'scope_mismatch = str(cfg.get("scope") or "") != VINTED_RADAR_SCOPE' in block
    assert 'await cancel_scan(fresh.id)' in block
    assert 'last_scan_at = None if scope_mismatch else cfg.get("last_scan_at")' in block


def test_cancel_marks_not_started_categories_terminal_so_legacy_queue_cannot_stick():
    block = LAB.split('async def cancel_scan(', 1)[1].split('\n\n\nasync def recalc_scan', 1)[0]
    assert 'VintedScanCategory.status == "queued"' in block
    assert '.values(status="cancelled", finished_at=now, updated_at=now)' in block
    assert 'await recalc_scan(scan_id)' in block


def test_item_leaf_catalog_is_preserved_when_catalog_response_exposes_it():
    assert '_int_or_none(_first(raw, ("catalog_id", "catalogId")))' in PROBE
    assert '_int_or_none(_nested(raw, "catalog", "id"))' in PROBE
    assert 'item_catalog_id = _int(getattr(item, "catalog_id", None), 0) or int(catalog_id)' in LAB


def test_admin_progress_calls_radar_jobs_segments_not_2400_leaf_categories():
    assert 'Radar-сегментов' in BOT
    assert 'непересекающихся сегментов' in BOT
    assert 'Сегменты: <b>' in BOT
    assert 'все конечные категории' not in BOT.split('if action == "radarstart":', 1)[1].split('if action == "radarstop":', 1)[0]
