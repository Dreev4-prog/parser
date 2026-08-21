import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseInvariantTests(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "4.6.4")

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

    def test_ai_worker_never_opens_parser_browser(self):
        source = (ROOT / "ai_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("KleinanzeigenParser", source)
        self.assertIn('REMOTE_VIEW_MANAGER.fetch', source)
        self.assertIn('AI_PAUSE_DURING_USER_SCANS', source)

    def test_everyday_scan_ui_hides_transport_and_regional_internals(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        start = source.index("def render_user_job_status")
        end = source.index("async def progress_ticker", start)
        block = source[start:end]
        self.assertNotIn("Отдельная Chromium-сессия", block)
        self.assertNotIn("Региональный добор даты", block)
        self.assertNotIn("Поиск даты", block)
        self.assertIn("Сканирование · {percent}%", block)
        self.assertIn("Собираю просмотры · {percent}%", block)

    def test_ai_shadow_is_independent_from_user_auto_measurements(self):
        source = (ROOT / "ai_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("UserSettings", source)
        self.assertNotIn("ScanObservation", source)
        self.assertIn("AI_CHECKPOINT_HOURS", source)


    def test_ai_popularity_is_separate_from_opportunity(self):
        source = (ROOT / "early_winner.py").read_text(encoding="utf-8")
        self.assertIn("Saturation is intentionally absent from this formula", source)
        self.assertIn('return "hot_product"', source)
        self.assertIn('return "saturated"', source)
        self.assertNotIn("- mass_penalty", source)

    def test_ai_v46_admin_sections_are_routable(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('callback_data="adminai:hidden"', source)
        self.assertIn('callback_data="adminai:momentum"', source)
        self.assertIn('{"new", "hidden", "momentum", "winners", "active", "confirmed", "rejected", "recent"}', source)

    def test_ai_v46_columns_have_additive_migration(self):
        source = (ROOT / "db.py").read_text(encoding="utf-8")
        self.assertIn("ai_opportunity_columns", source)
        self.assertIn("ADD COLUMN IF NOT EXISTS", source)
        self.assertIn('"opportunity_type"', source)
        self.assertIn('"saturation_score"', source)


    def test_v461_idle_browser_shutdown_is_conservative(self):
        idle = (ROOT / "browser_idle.py").read_text(encoding="utf-8")
        self.assertIn("depth is None or depth > 0", idle)
        self.assertIn("async with self.activity_lock", idle)
        self.assertIn("shutdown_shared_browser_runtime", idle)
        parser = (ROOT / "parser.py").read_text(encoding="utf-8")
        self.assertIn("self._shared_browser_generation != _SHARED_BROWSER_FLEET.generation", parser)
        for worker in ("page_worker.py", "date_worker.py", "view_counter_worker.py"):
            source = (ROOT / worker).read_text(encoding="utf-8")
            self.assertIn("BrowserIdleShutdownGuard", source)
            self.assertIn("600", source)


    def test_v462_ai_notifications_live_in_lab_badge_not_chat_pushes(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('AI_BADGE_EVENT_TYPES = ("winner", "confirmed")', source)
        self.assertIn('callback_data="adminai:new"', source)
        self.assertIn('DT AI Lab 🔴', source)
        self.assertIn('async def _mark_ai_signals_seen', source)
        self.assertNotIn('name="ai-admin-notification-scheduler"', source)
        worker = (ROOT / "ai_worker.py").read_text(encoding="utf-8")
        self.assertIn('candidate.outcome == "confirmed"', worker)


    def test_v464_admin_workers_are_consolidated_and_active_scans_visible(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('text="⚙️ Воркеры", callback_data="adminworkers"', source)
        self.assertIn('callback_data="adminactive"', source)
        self.assertIn('async def _admin_workers_text()', source)
        self.assertIn('async def _admin_active_scans_text', source)
        self.assertIn('UserScan.status == "running"', source)
        main_start = source.index('def admin_keyboard(')
        main_end = source.index('def admin_back_keyboard', main_start)
        main = source[main_start:main_end]
        self.assertNotIn('callback_data="admindates"', main)
        self.assertNotIn('callback_data="adminpages"', main)
        self.assertNotIn('callback_data="adminviews"', main)



if __name__ == "__main__":
    unittest.main()
