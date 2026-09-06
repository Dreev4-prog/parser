from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    a = BOT.index(start)
    b = BOT.index(end, a)
    return BOT[a:b]


def test_idle_turbo_is_opt_in_by_health_and_zero_foreground_users():
    helper = _block("async def _radar_autoscan_idle_turbo_available", "async def _notify_radar_autoscan_admins")
    assert "RADAR_AUTOSCAN_IDLE_TURBO_ENABLED" in helper
    assert "running, queued = await _radar_foreground_counts()" in helper
    assert "if running or queued:" in helper
    assert "snap = await TRAFFIC.snapshot()" in helper
    assert "snap.penalty_level" in helper
    assert "snap.cooldown_seconds" in helper


def test_idle_turbo_borrows_view_and_page_capacity_without_parallel_categories():
    assert 'RADAR_AUTOSCAN_IDLE_VIEW_CONCURRENCY = _radar_env_int("RADAR_AUTOSCAN_IDLE_VIEW_CONCURRENCY", 8' in BOT
    assert 'RADAR_AUTOSCAN_IDLE_PREFETCH_PAGES = _radar_env_int("RADAR_AUTOSCAN_IDLE_PREFETCH_PAGES", 16' in BOT
    prefetch = _block("async def top_up_direct_prefetch", "await top_up_direct_prefetch(candidate)")
    assert "user_id == RADAR_AUTOSCAN_USER_ID" in prefetch
    assert "await _radar_autoscan_idle_turbo_available()" in prefetch
    assert "window_pages=prefetch_window" in prefetch
    # The round runner stays one-category-at-a-time; no gather/task fan-out was added.
    runner = _block("async def _run_radar_autoscan_round", "async def radar_autoscan_scheduler")
    assert "for idx in range" in runner or "while" in runner
    assert "asyncio.gather(*category" not in runner


def test_large_exact_unknown_tail_gets_targeted_idle_repair_before_full_rescan():
    enrich = _block("async def enrich_autoscan_view_counts", "async def refresh_view_counts")
    assert "Radar AutoScan idle fleet repair" in enrich
    assert "REMOTE_VIEW_MANAGER.fetch(" in enrich and "unresolved," in enrich
    assert "RADAR_AUTOSCAN_IDLE_REMOTE_RETRY_MAX" in enrich
    assert "RADAR_AUTOSCAN_IDLE_VIEW_REPAIR_MAX" in enrich
    assert "Radar AutoScan idle exact repair yielded to foreground user" in enrich
    assert "browser_fallback=True" in enrich
    assert "accurate=True" in enrich


def test_accuracy_gates_are_not_relaxed():
    assert "RADAR_AUTOSCAN_MIN_VIEW_COVERAGE_PCT = 99.0" in BOT
    assert "RADAR_AUTOSCAN_VIEW_SOFT_TAIL_MAX = 8" in BOT
    usable = _block("def _radar_autoscan_views_usable", "# v4.12.0 Daily Radar")
    assert "coverage_pct >= RADAR_AUTOSCAN_MIN_VIEW_COVERAGE_PCT" in usable
    assert "missing <= _radar_autoscan_view_tail_budget(requested)" in usable
    enrich = _block("async def enrich_autoscan_view_counts", "async def refresh_view_counts")
    assert "row.view_count = None" in enrich
    assert "vr.views is None" in enrich


def test_review_breakdown_is_persisted_and_visible():
    for key in ("review_views", "review_pages", "review_watchdog", "review_gate", "review_transport", "review_other"):
        assert f'"{key}": 0' in BOT
    assert 'state["review_views"]' in BOT
    assert 'state["review_watchdog"]' in BOT
    assert 'review_parts.append(f"👁 views {review_views}")' in BOT
    assert 'review_parts.append(f"📄 pages {review_pages}")' in BOT
    assert 'review_parts.append(f"🌐 transport {review_transport}")' in BOT
    assert "защитный режим Kleinanzeigen" in BOT
    assert "Idle Turbo" in BOT
