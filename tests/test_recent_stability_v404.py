from pathlib import Path

BOT = Path('bot.py').read_text(encoding='utf-8')


def test_stable_locator_returns_retrying_fetcher():
    assert 'page_fetch = stable_fetch if STABLE_SCAN_ENGINE else fetch' in BOT
    assert '"fetch": page_fetch' in BOT


def test_recent_sparse_pages_can_advance_depth_after_target_window():
    assert 'if not target_on_page and (target_seen_any or today_fast_path):' in BOT
    assert 'direct_pages_collected += 1' in BOT


def test_recent_target_does_not_fan_out_to_hidden_regions_at_public_limit():
    assert 'if recent_fast_path and target_seen_any:' in BOT
    assert 'свежая дата обработана до публичного лимита' in BOT


def test_recent_ui_starts_in_collection_phase():
    assert 'phase="collecting" if recent_fast_path else "jumping"' in BOT
