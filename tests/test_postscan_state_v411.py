from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_worker_day_key_exists_and_uses_scan_timezone():
    assert "def berlin_date_key() -> str:" in BOT
    block = BOT[BOT.index("def berlin_date_key"):BOT.index("def berlin_today_utc_bounds")]
    assert "datetime.now(MOSCOW).date().isoformat()" in block


def test_category_state_write_cannot_destroy_successful_scan():
    start = BOT.index("# CategoryScanState is an optimization/checkpoint summary")
    end = BOT.index("result = ScanResult(", start)
    block = BOT[start:end]
    assert "try:" in block
    assert "await save_category_scan_state(" in block
    assert "except Exception:" in block
    assert "Non-fatal CategoryScanState save failed" in block
