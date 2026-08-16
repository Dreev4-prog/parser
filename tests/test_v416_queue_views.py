from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_view_threshold_is_deferred_until_after_page_crawl():
    assert "deferred_view_items: dict[str, ParsedListing]" in BOT
    assert "Deferred view-count phase failed" in BOT
    # Regression: process_target_items must not block each page on view requests.
    block = BOT.split("async def process_target_items", 1)[1].split("async def locate_feed", 1)[0]
    assert "await enrich_page_view_counts" not in block


def test_distributed_queue_has_live_user_ticker():
    assert "async def distributed_queue_ui_ticker" in BOT
    assert "Позиция в очереди" in BOT
    assert "distributed-queue-ui-ticker" in BOT


def test_stale_distributed_queue_is_retired():
    assert "async def cleanup_stale_distributed_queue_rows" in BOT
    assert "DISTRIBUTED_STALE_QUEUE_SECONDS" in BOT


def test_claim_log_contains_telegram_linkage():
    assert "Distributed job claimed job=%s consumer=%s scan_id=%s user=%s chat=%s message=%s" in BOT
