from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _const(name: str) -> int:
    match = re.search(rf"^{name}\s*=\s*(\d+)\s*$", BOT, re.MULTILINE)
    assert match, f"missing {name}"
    return int(match.group(1))


def test_radar_fresh_and_context_are_exactly_15_pages_each():
    assert _const("RADAR_AUTOSCAN_DEPTH") == 15
    assert _const("RADAR_CONTEXT_DEPTH") == 15


def test_completed_manual_or_daily_fresh_auto_starts_yesterday_context():
    finish_start = BOT.index("async def _radar_autoscan_finish_round")
    finish_end = BOT.index("async def _radar_autoscan_interruptible_sleep", finish_start)
    finish = BOT[finish_start:finish_end]
    assert 'mode in {"manual", "daily"}' in finish
    assert "_radar_autoscan_new_context_round(current, now.date())" in finish
    assert "_radar_autoscan_wakeup.set()" in finish


def test_context_round_targets_exactly_yesterday():
    start = BOT.index("def _radar_autoscan_new_context_round")
    end = BOT.index("def _radar_autoscan_retry_round", start)
    context = BOT[start:end]
    assert "target_day = context_day - timedelta(days=1)" in context
    assert '"target_date": target_day.isoformat()' in context


def test_release_docs_describe_yesterday_15_page_context():
    assert "15 verified pages/category for yesterday" in README
