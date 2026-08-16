from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_v403_version():
    assert (ROOT / "VERSION").read_text().strip() == "4.0.3"
    assert 'APP_VERSION = "4.0.3"' in BOT


def test_recent_dates_use_stream_fast_path():
    assert 'recent_fast_path = 0 <= (moscow_today - target_day).days <= 2' in BOT
    assert 'if recent_fast_path and feed_name == "nationwide"' in BOT
    assert 'candidate_page=1' in BOT


def test_recent_stream_can_walk_whole_public_window():
    assert 'hard_stop = limit if recent_fast_path else min(' in BOT


def test_sparse_recent_timestamp_pages_do_not_immediately_fail():
    assert 'if recent_fast_path and relation == "unknown" and items:' in BOT
