from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
PARSER = (ROOT / "parser.py").read_text(encoding="utf-8")
WORKER = (ROOT / "hybrid_worker.py").read_text(encoding="utf-8")


def test_v372_version():
    assert (ROOT / "VERSION").read_text().strip() == "3.8.0"
    assert 'APP_VERSION = "3.8.0"' in BOT


def test_hybrid_is_http_first_with_hard_watchdog():
    assert 'HYBRID_HTTP_FIRST' in PARSER
    assert '_fetch_hybrid_direct_http_document' in PARSER
    assert 'asyncio.wait_for(' in PARSER
    assert 'HYBRID_WATCHDOG_SECONDS' in PARSER
    start = PARSER.index('async def _fetch_scan_hybrid_document')
    direct = PARSER.index('_fetch_hybrid_direct_http_document(url)', start)
    browser = PARSER.index('_bootstrap_hybrid_session(url)', direct)
    assert direct < browser


def test_partial_result_is_auto_recovered_before_user_partial_state():
    assert 'async def auto_recover_partial_category' in BOT
    assert 'force_refresh=True' in BOT
    process = BOT.index('async def process_scan_job')
    recover = BOT.index('auto_recover_partial_category(bot, job, cat, dispatched)', process)
    incomplete = BOT.index('if not result.date_complete:', recover)
    assert recover < incomplete
    assert 'SCAN_AUTO_RECOVERY_PASSES' in BOT


def test_verified_pages_are_checkpointed_for_incremental_recovery():
    assert '_scan_page_checkpoints' in PARSER
    assert 'SCAN_PAGE_CHECKPOINT_TTL_SECONDS' in PARSER
    assert 'strong_enough = (' in PARSER
    assert 'not bool(getattr(info, "suspicious", False))' in PARSER
    assert 'scan_page_checkpoint_hits' in PARSER
    assert 'live_req.checkpoint_hits += 1' in BOT


def test_user_can_see_live_wait_and_recovery_status():
    assert 'Ответ Kleinanzeigen' in BOT
    assert 'Автовосстановление скана' in BOT
    assert 'Автоповторов по таймауту' in BOT
    assert 'Требуют ручной проверки' in BOT


def test_hybrid_worker_uses_stability_profile():
    assert 'HYBRID_HTTP_FIRST", "1"' in WORKER
    assert 'HYBRID_WATCHDOG_SECONDS", "15"' in WORKER
    assert 'SCAN_AUTO_RECOVERY_PASSES", "3"' in WORKER
    assert 'SCAN_PAGE_CHECKPOINT_TTL_SECONDS", "900"' in WORKER
