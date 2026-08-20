import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseInvariantTests(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "4.4.0")

    def test_date_window_and_timezone_semantics_were_not_changed(self):
        source = (ROOT / "date_manager.py").read_text(encoding="utf-8")
        self.assertIn("DATE_MAX_AGE_DAYS = 4", source)
        self.assertIn('ZoneInfo("Europe/Moscow")', source)

    def test_watchdog_was_not_changed(self):
        source = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("SCAN_CATEGORY_HARD_TIMEOUT_SECONDS=1200", source)

    def test_upsert_owns_its_write_lock(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        start = source.index("async def upsert_page_items(")
        end = source.index("async def mark_promoted_listings", start)
        self.assertIn("async with db_write_lock", source[start:end])

    def test_date_boundary_race_refinement_is_kept(self):
        source = (ROOT / "date_manager.py").read_text(encoding="utf-8")
        self.assertIn("v4.3.37 DATE BOUNDARY RACE FIX", source)
        self.assertIn("for _refine_round in range(2):", source)


if __name__ == "__main__":
    unittest.main()
