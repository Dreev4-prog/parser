from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_v410_version():
    assert (ROOT / "VERSION").read_text().strip() == "4.1.5"
    assert 'APP_VERSION = "4.1.5"' in BOT


def test_all_dates_use_one_stable_stream():
    assert 'universal_date_stream = bool(STABLE_SCAN_ENGINE)' in BOT
    assert 'one deterministic newest-sorted locator for every date/feed' in BOT
    assert 'if recent_fast_path and feed_name == "nationwide"' not in BOT


def test_universal_stream_can_walk_whole_public_window():
    assert 'hard_stop = limit' in BOT


def test_sparse_timestamp_pages_are_not_hard_failures():
    assert 'Sparse/hidden timestamp templates are not a broken page' in BOT
    assert 'if relation == "unknown":' in BOT
