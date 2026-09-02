import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseStaticTests(unittest.TestCase):
    def test_all_python_files_parse(self):
        for path in ROOT.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_release_version(self):
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "4.21.15")

    def test_radar3_autoscan_is_today_only_and_baseline_only(self):
        bot = (ROOT / "bot.py").read_text(encoding="utf-8")
        radar = (ROOT / "radar.py").read_text(encoding="utf-8")
        self.assertIn("RADAR_AUTOSCAN_DEPTH = 20", bot)
        self.assertIn("RADAR_CONTEXT_ENABLED = False", bot)
        self.assertIn("A first counter never votes", radar)
        self.assertIn("admitted=0, saved=0", radar)

    def test_old_synthetic_top_position_score_is_gone(self):
        radar = (ROOT / "radar.py").read_text(encoding="utf-8")
        self.assertNotIn('58 + percentile * 20', radar)
        self.assertNotIn('view_bonus', radar)
        self.assertNotIn('def _status_for_score', radar)

    def test_unified_radar_fields_have_additive_db_migrations(self):
        models = (ROOT / "models.py").read_text(encoding="utf-8")
        db = (ROOT / "db.py").read_text(encoding="utf-8")
        for field in ("radar_rank", "demand_views", "demand_age_minutes", "demand_gate"):
            self.assertIn(field, models)
            self.assertIn(f'"{field}"', db)
        self.assertIn("demand_status", models)
        self.assertIn('"demand_status"', db)

    def test_radar_score_has_no_post_model_signal_bonus(self):
        radar = (ROOT / "radar.py").read_text(encoding="utf-8")
        self.assertNotIn('repeat_bonus =', radar)
        self.assertNotIn('confirmed_bonus =', radar)
        self.assertIn('return _clamp_score(int(product.last_signal_score or 0))', radar)

    def test_stale_hot_signal_ages_against_demand_gate(self):
        radar = (ROOT / "radar.py").read_text(encoding="utf-8")
        self.assertIn('effective_age_minutes = (', radar)
        self.assertIn('_snapshot_live_evidence', radar)

    def test_observed_delta_does_not_rejuvenate_listing_age_cohort(self):
        radar = (ROOT / "radar.py").read_text(encoding="utf-8")
        feature = (ROOT / "early_winner.py").read_text(encoding="utf-8")
        self.assertIn('age_minutes, exact_clock = listing_age_minutes(listing.posted_text, when)', radar)
        self.assertIn('velocity_window_minutes = (', radar)
        self.assertIn('velocity_window_minutes: float | None = None', feature)

    def test_hard_stop_and_watchdog_survived_consolidation(self):
        bot = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("RadarAutoScanStopped", bot)
        self.assertIn("RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS", bot)
        self.assertIn("_radar_autoscan_stop_event", bot)
        self.assertIn("background_pause_started", bot)

    def test_root_has_no_historical_deploy_heap(self):
        self.assertEqual(list(ROOT.glob("DEPLOY_V4_*.md")), [])
        self.assertEqual(list(ROOT.glob("*_SHA256.txt")), [])
        self.assertTrue((ROOT / "docs" / "releases" / "HISTORY.md").is_file())
        self.assertTrue((ROOT / "docs" / "checksums" / "HISTORY_SHA256.txt").is_file())
        self.assertEqual(list((ROOT / "docs" / "releases").glob("DEPLOY_V4_*.md")), [])

    def test_no_duplicate_top_level_definitions(self):
        for path in ROOT.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            seen = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.assertNotIn(node.name, seen, f"duplicate top-level definition {node.name} in {path}")
                    seen[node.name] = node.lineno

    def test_manual_round_does_not_queue_yesterday_context(self):
        bot = (ROOT / "bot.py").read_text(encoding="utf-8")
        scheduler = bot.split("async def radar_autoscan_scheduler", 1)[1].split("async def send_smart_export", 1)[0]
        self.assertNotIn("_radar_autoscan_new_context_round", scheduler)
        self.assertIn("RADAR_AUTOSCAN_DEPTH = 20", bot)

    def test_card_cache_schema_matches_manager_worker_and_stable_payload(self):
        page = (ROOT / "page_manager.py").read_text(encoding="utf-8")
        worker = (ROOT / "page_worker.py").read_text(encoding="utf-8")
        stable = (ROOT / "stable_engine.py").read_text(encoding="utf-8")
        marker = "v4200-core2-audit3"
        self.assertIn(f'PAGE_CACHE_SCHEMA = "{marker}"', page)
        self.assertIn('PAGE_CACHE_SCHEMA,', worker)
        self.assertIn('f"{PAGE_REDIS_PREFIX}:cache:{PAGE_CACHE_SCHEMA}:{cache_id}"', worker)
        self.assertIn(f'STABLE_PAGE_PAYLOAD_SCHEMA = "{marker}"', stable)

    def test_date_probe_cache_schema_matches_manager_and_worker(self):
        manager = (ROOT / "date_manager.py").read_text(encoding="utf-8")
        worker = (ROOT / "date_worker.py").read_text(encoding="utf-8")
        marker = "v4200-core2-audit3"
        self.assertIn(f'DATE_CACHE_SCHEMA = "{marker}"', manager)
        self.assertIn('DATE_CACHE_SCHEMA,', worker)
        self.assertIn('f"{DATE_REDIS_PREFIX}:cache:{DATE_CACHE_SCHEMA}:{cache_id}"', worker)

    def test_legacy_ai_service_is_retired(self):
        launcher = (ROOT / "service_launcher.py").read_text(encoding="utf-8")
        self.assertIn('"ai-worker": "retired_ai_worker.py"', launcher)
        self.assertTrue((ROOT / "retired_ai_worker.py").is_file())


if __name__ == "__main__":
    unittest.main()

class ViewWorkerFailFastStaticTests(unittest.TestCase):
    def test_malformed_view_stream_payload_publishes_failed_result(self):
        source = (ROOT / "view_counter_worker.py").read_text(encoding="utf-8")
        self.assertIn("_fail_stream_message_for_local_fallback", source)
        self.assertIn("view payload missing", source)
        self.assertIn("view payload invalid or empty", source)
        self.assertIn('"failed": True', source)

class AutoScanForegroundTrafficStaticTests(unittest.TestCase):
    def test_autoscan_exact_views_are_foreground_not_background(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('autoscan_view_priority = "scan_inline"', source)


def test_foreground_radar_detail_gate_never_infers_background_from_policy():
    text = Path("radar.py").read_text(encoding="utf-8")
    assert 'else "normal"' in text
    assert 'else ("normal" if int(getattr(TRAFFIC, "background_during_scans"' not in text


def test_verified_velocity_scheduler_keeps_background_priority_explicit():
    bot = Path("bot.py").read_text(encoding="utf-8")
    radar = Path("radar.py").read_text(encoding="utf-8")
    assert 'newly_verified_ids, traffic_priority="background"' in bot
    assert 'traffic_priority: str = "normal"' in radar
    assert 'force_priority=("background" if traffic_priority == "background" else "normal")' in radar

class ExactViewCompletenessStaticTests(unittest.TestCase):
    def test_non_autoscan_view_persistence_treats_omitted_urls_as_unknown(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        # enrich_page_view_counts + refresh_view_counts must iterate requested targets,
        # not only keys returned by a possibly partial result map.
        self.assertGreaterEqual(source.count("for item in targets:"), 2)
        self.assertGreaterEqual(source.count("vr = results.get(url)"), 2)
        self.assertGreaterEqual(source.count("if vr is None or vr.views is None:"), 2)
