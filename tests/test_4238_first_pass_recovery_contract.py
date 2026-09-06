from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    a = BOT.index(start)
    b = BOT.index(end, a)
    return BOT[a:b]


def test_stable_user_scan_repairs_partial_before_asking_for_second_launch():
    assert "SCAN_CATEGORY_ATTEMPTS = 2" in BOT
    assert "SCAN_AUTO_RECOVERY_PASSES = 1" in BOT
    recovery = _block("async def auto_recover_partial_category", "async def process_scan_job")
    assert "reset_scan_browser_context()" in recovery
    assert "parser.prepare_category_scan()" in recovery
    assert "force_refresh=True" in recovery
    assert "verified page checkpoints" in recovery or "verified" in recovery


def test_partial_user_card_retries_only_failed_categories():
    finish = _block("async def finish_job", "async def dispatch_category_with_retry")
    assert 'callback_data=f"scanrecheck:{job.scan_id}"' in finish
    assert "Повторить только проблемные" in finish
    assert "контрольный проход" in finish


def test_exception_retry_gets_clean_context_too():
    retry = _block("async def dispatch_category_with_retry", "async def auto_recover_partial_category")
    assert "SCAN_CATEGORY_ATTEMPTS" in retry
    assert "reset_scan_browser_context()" in retry
    assert "parser.prepare_category_scan()" in retry


def test_radar_repairs_retryable_category_inside_same_round():
    repair = _block("async def _radar_autoscan_inline_recover_category", "def _radar_autoscan_live_stage_line")
    assert '{"partial", "radar_views"}' in repair
    assert "TemporaryAccessError" in repair
    assert "_radar_foreground_counts()" in repair
    assert "reset_scan_browser_context()" in repair
    assert "parser.prepare_category_scan()" in repair
    assert "_radar_autoscan_interruptible_sleep" in repair
    assert "previous_result.matched_ids" in repair
    runner = _block("async def _run_radar_autoscan_round_inner", "async def _run_radar_autoscan_round(bot")
    assert "_radar_autoscan_inline_recover_category(" in runner
    assert 'state["inline_recovered"]' in runner


def test_radar_pressure_is_not_mislabeled_as_user_priority():
    ui = _block("async def _radar_autoscan_text", "def admin_radar_autoscan_keyboard")
    assert "защитный режим Kleinanzeigen" in ui
    assert "активных {fg_running}" in ui
    assert "отказов 60с" in ui
    assert "review_transport" in ui


def test_existing_review_reasons_are_reclassified_after_upgrade():
    normalizer = _block("def _radar_autoscan_normalize_state", "async def load_radar_autoscan_state")
    assert 'if "review_transport" not in raw_state:' in normalizer
    for token in ("http 403", "http 429", "таймаут", "timeout", "огранич"):
        assert token in normalizer
    assert 'inferred_breakdown["review_transport"]' in normalizer


def test_idle_prefetch_is_fast_but_not_whole_category_burst():
    assert 'RADAR_AUTOSCAN_IDLE_PREFETCH_PAGES = _radar_env_int("RADAR_AUTOSCAN_IDLE_PREFETCH_PAGES", 16' in BOT
    assert "RADAR_AUTOSCAN_DEPTH = 20" in BOT


def test_accuracy_gates_remain_fail_closed():
    assert "RADAR_AUTOSCAN_MIN_VIEW_COVERAGE_PCT = 99.0" in BOT
    assert "RADAR_AUTOSCAN_VIEW_SOFT_TAIL_MAX = 8" in BOT
    enrich = _block("async def enrich_autoscan_view_counts", "async def refresh_view_counts")
    assert "row.view_count = None" in enrich
    assert "vr.views is None" in enrich
