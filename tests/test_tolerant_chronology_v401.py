from datetime import date
from pathlib import Path

from parser import ParsedListing, profile_page_dates

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
PARSER = (ROOT / "parser.py").read_text(encoding="utf-8")
FLEET = (ROOT / "fleet_worker.py").read_text(encoding="utf-8")


def _item(i: int, posted: str | None):
    return ParsedListing(str(i), f"item {i}", "10 €", 10, f"https://example/{i}", posted)


def test_v401_version():
    assert (ROOT / "VERSION").read_text().strip() == "4.1.4"
    assert 'APP_VERSION = "4.1.4"' in BOT


def test_sparse_direction_uses_absolute_date_evidence():
    rows = [_item(1, "15.08.2026"), _item(2, "15.08.2026")] + [_item(i, None) for i in range(3, 20)]
    profile = profile_page_dates(rows, date(2026, 8, 16))
    assert profile.relation == "older"
    assert profile.parsed_count == 2


def test_mixed_boundary_is_not_unknown_failure():
    rows = [_item(1, "17.08.2026"), _item(2, "15.08.2026")] + [_item(i, None) for i in range(3, 15)]
    profile = profile_page_dates(rows, date(2026, 8, 16))
    assert profile.relation == "mixed"


def test_unknown_does_not_automatically_trigger_hidden_fill():
    assert 'Unknown chronology is a parser-quality issue, not proof that the' in BOT
    assert 'reason = nationwide.get("reason")' in BOT
    assert 'STABLE_WEAK_PAGE_GAP_LIMIT' in BOT


def test_fleet_uses_tolerant_chronology_defaults():
    assert 'MIN_PAGE_DATE_COVERAGE", "0.20"' in FLEET
    assert 'MIN_DIRECTION_DATED_ITEMS", "2"' in FLEET
