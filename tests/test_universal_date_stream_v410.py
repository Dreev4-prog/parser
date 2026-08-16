from pathlib import Path
from datetime import date

from parser import ParsedListing, profile_page_dates

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
PARSER = (ROOT / "parser.py").read_text(encoding="utf-8")


def row(idx: int, posted: str | None):
    return ParsedListing(str(idx), f"item {idx}", "100 €", 100, f"https://www.kleinanzeigen.de/s-anzeige/x/{idx}", posted)


def test_v410_uses_one_date_algorithm_for_all_dates():
    assert 'APP_VERSION = "4.1.2"' in BOT
    assert 'universal_date_stream = bool(STABLE_SCAN_ENGINE)' in BOT
    assert 'one deterministic newest-sorted locator for every date/feed' in BOT
    assert 'if recent_fast_path and feed_name == "nationwide"' not in BOT


def test_unknown_dates_are_not_a_partial_condition_anymore():
    locate = BOT[BOT.index('if STABLE_SCAN_ENGINE:'):BOT.index('low_newer = 0', BOT.index('if STABLE_SCAN_ENGINE:'))]
    assert 'Sparse/hidden timestamp templates are not a broken page' in locate
    assert 'weak_streak > STABLE_WEAK_PAGE_GAP_LIMIT' not in locate


def test_collection_only_stops_on_real_invalid_page():
    block = BOT[BOT.index('async def collect_direct'):BOT.index('async def hidden_fill')]
    assert 'return "invalid_stop", direct_pages_collected' in block
    assert 'return "weak_stop", direct_pages_collected' not in block


def test_browser_uses_rendered_dom():
    block = PARSER[PARSER.index('async def _fetch_scan_browser_document'):PARSER.index('async def _close_browser_runtime')]
    assert 'wait_for_selector' in block
    assert 'await page.content()' in block
    assert block.index('await page.content()') < block.index('await response.text()')


def test_profile_arbitrary_historical_date_is_directional():
    target = date(2026, 8, 14)
    assert profile_page_dates([row(1, '16.08.2026'), row(2, '15.08.2026')], target).relation == 'newer'
    assert profile_page_dates([row(3, '14.08.2026'), row(4, None)], target).relation == 'target'
    assert profile_page_dates([row(5, '13.08.2026'), row(6, '12.08.2026')], target).relation == 'older'
