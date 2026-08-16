from pathlib import Path

BOT = Path('bot.py').read_text(encoding='utf-8')
PARSER = Path('parser.py').read_text(encoding='utf-8')


def test_stable_locator_returns_retrying_fetcher():
    assert 'page_fetch = stable_fetch if STABLE_SCAN_ENGINE else fetch' in BOT
    assert '"fetch": page_fetch' in BOT


def test_unknown_chronology_does_not_increment_confirmed_depth_without_target():
    block = BOT[BOT.index('async def collect_direct'):BOT.index('async def hidden_fill')]
    assert 'if relation == "unknown":' in block
    assert 'if target_on_page:' in block
    assert 'page += 1' in block


def test_only_persistent_invalid_page_stops_direct_pass():
    block = BOT[BOT.index('async def collect_direct'):BOT.index('async def hidden_fill')]
    assert 'return "invalid_stop", direct_pages_collected' in block
    assert 'return "weak_stop", direct_pages_collected' not in block


def test_browser_parses_rendered_dom_not_navigation_body():
    browser = PARSER[PARSER.index('async def _fetch_scan_browser_document'):PARSER.index('async def _close_browser_runtime')]
    assert 'await page.content()' in browser
    assert 'wait_for_selector' in browser
    assert browser.index('await page.content()') < browser.index('await response.text()')
