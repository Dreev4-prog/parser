import os
import unittest
from unittest.mock import patch

from db_url import normalize_database_url
import service_launcher


class DatabaseUrlTests(unittest.TestCase):
    def test_railway_postgres_url_uses_asyncpg(self):
        value = normalize_database_url("postgresql://user:pass@host:5432/db")
        self.assertTrue(value.startswith("postgresql+asyncpg://"))

    def test_sslmode_is_normalized(self):
        value = normalize_database_url("postgres://u:p@h/db?sslmode=require")
        self.assertIn("ssl=require", value)
        self.assertNotIn("sslmode=", value)


class ServiceLauncherRoleTests(unittest.TestCase):
    def role_for(self, name):
        with patch.dict(os.environ, {"RAILWAY_SERVICE_NAME": name}, clear=True):
            return service_launcher._role()

    def test_all_railway_worker_names_route_correctly(self):
        expected = {
            "parser": "bot",
            "Page Worker": "page-worker",
            "Date Worker": "date-worker",
            "View Worker": "view-worker",
            "AI Worker": "ai-worker",
            "Lifecycle Worker": "lifecycle-worker",
            "Vinted Probe": "vinted-probe",
            "Vinted Worker": "vinted-probe",
        }
        for name, role in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.role_for(name), role)


if __name__ == "__main__":
    unittest.main()

class RollingDeployIsolationTests(unittest.TestCase):
    def test_runtime_namespaces_are_release_scoped(self):
        import page_manager, date_manager, view_manager
        for value in (
            page_manager.PAGE_RUNTIME_PREFIX,
            date_manager.DATE_RUNTIME_PREFIX,
            view_manager.VIEW_RUNTIME_PREFIX,
        ):
            self.assertIn("runtime:v4200-core2-audit3", value)

    def test_database_init_uses_postgres_advisory_migration_lock(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "db.py").read_text(encoding="utf-8")
        self.assertIn("pg_advisory_xact_lock", source)
        self.assertIn("DB_MIGRATION_ADVISORY_LOCK_KEY", source)

class PagePayloadRoundTripTests(unittest.TestCase):
    def test_page_payload_preserves_integrity_fields(self):
        from page_manager import serialize_page_info, deserialize_page_info
        from parser import CategoryPageInfo, ParsedListing
        item = ParsedListing(
            external_id="3499999999", title="Test", price_text="149 €", price_eur=149,
            url="https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
            posted_text="Heute, 10:00", is_price_reduced=True,
        )
        info = CategoryPageInfo(
            requested_page=3, final_url="https://www.kleinanzeigen.de/s-test/seite:3/c225",
            items=[item], result_start=61, result_end=90, total_results=900,
            actual_page=3, max_page=30, request_matches_page=True, page_verified=True,
            fingerprint="abc", raw_candidates=2, promoted_filtered=1,
            promoted_ids=["3488888888"], price_reduced_filtered=1,
            price_reduced_ids=["3499999999"], duplicate_cards=0,
            missing_date_count=0, missing_price_count=0, date_coverage=1.0,
            suspicious=False, warnings=["w"], location_shards=[("https://www.kleinanzeigen.de/s-x/c225l1", 12)],
        )
        restored = deserialize_page_info(serialize_page_info(info))
        self.assertIsNotNone(restored)
        self.assertEqual(restored.requested_page, 3)
        self.assertTrue(restored.page_verified)
        self.assertEqual(restored.promoted_ids, ["3488888888"])
        self.assertEqual(restored.price_reduced_ids, ["3499999999"])
        self.assertTrue(restored.items[0].is_price_reduced)
        self.assertEqual(restored.location_shards[0][1], 12)

class RemoteViewContractIdentityTests(unittest.TestCase):
    def test_remote_official_counter_must_match_requested_ad(self):
        from view_manager import _remote_exact_result_identity_ok
        req = "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234"
        self.assertTrue(_remote_exact_result_identity_ok(
            req, "verified-official:direct-http:s-vac-inc-get:official",
            "https://www.kleinanzeigen.de/s-vac-inc-get.json?adId=3499999999",
        ))
        self.assertFalse(_remote_exact_result_identity_ok(
            req, "verified-official:direct-http:s-vac-inc-get:official",
            "https://www.kleinanzeigen.de/s-vac-inc-get.json?adId=3488888888",
        ))

    def test_remote_browser_result_must_finish_on_exact_listing(self):
        from view_manager import _remote_exact_result_identity_ok
        req = "https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234"
        self.assertTrue(_remote_exact_result_identity_ok(
            req, "verified-dom:#viewad-cntr-num", req,
        ))
        self.assertFalse(_remote_exact_result_identity_ok(
            req, "verified-dom:#viewad-cntr-num",
            "https://www.kleinanzeigen.de/s-anzeige/other/3488888888-225-1234",
        ))
        self.assertFalse(_remote_exact_result_identity_ok(
            req, "verified-dom:#viewad-cntr-num",
            "https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225",
        ))

class PagePayloadTrustBoundaryTests(unittest.TestCase):
    def _valid_payload(self):
        from page_manager import serialize_page_info
        from parser import CategoryPageInfo, ParsedListing
        info = CategoryPageInfo(
            requested_page=1,
            final_url="https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225",
            items=[ParsedListing(
                external_id="3499999999",
                title="Test",
                price_text="149 €",
                price_eur=149,
                url="https://www.kleinanzeigen.de/s-anzeige/test/3499999999-225-1234",
                posted_text="Heute, 10:00",
            )],
            page_verified=True,
        )
        return serialize_page_info(info)

    def test_page_payload_rejects_external_id_url_mismatch(self):
        import json
        from page_manager import deserialize_page_info
        payload = json.loads(self._valid_payload())
        payload["items"][0]["external_id"] = "3488888888"
        self.assertIsNone(deserialize_page_info(json.dumps(payload)))

    def test_page_payload_rejects_foreign_listing_host(self):
        import json
        from page_manager import deserialize_page_info
        payload = json.loads(self._valid_payload())
        payload["items"][0]["url"] = "https://example.com/s-anzeige/test/3499999999-225-1234"
        self.assertIsNone(deserialize_page_info(json.dumps(payload)))

    def test_page_payload_rejects_foreign_final_url(self):
        import json
        from page_manager import deserialize_page_info
        payload = json.loads(self._valid_payload())
        payload["final_url"] = "https://example.com/s-pc-zubehoer-software/c225"
        self.assertIsNone(deserialize_page_info(json.dumps(payload)))

    def test_stable_payload_uses_same_identity_validation(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "stable_engine.py").read_text(encoding="utf-8")
        self.assertIn("from page_manager import deserialize_page_info", source)
        self.assertIn("info = deserialize_page_info(", source)
        self.assertIn("invalid stable-page payload identity", source)

class PagePayloadShardSafetyTests(unittest.TestCase):
    def test_foreign_or_non_location_shards_are_not_replayed(self):
        import json
        from page_manager import deserialize_page_info
        from parser import CategoryPageInfo, ParsedListing
        from page_manager import serialize_page_info
        info = CategoryPageInfo(
            requested_page=1,
            final_url="https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225",
            items=[ParsedListing(
                external_id="3499999999", title="x", price_text="1 €", price_eur=1,
                url="https://www.kleinanzeigen.de/s-anzeige/x/3499999999-225-1234",
                posted_text="Heute, 10:00",
            )],
            location_shards=[("https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225l1234", 10)],
        )
        payload = json.loads(serialize_page_info(info))
        payload["location_shards"].extend([
            ["https://example.com/c225l9999", 99],
            ["https://www.kleinanzeigen.de/s-anzeige/x/3499999999-225-1234", 88],
        ])
        restored = deserialize_page_info(json.dumps(payload))
        self.assertIsNotNone(restored)
        self.assertEqual(restored.location_shards, [("https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225l1234", 10)])

class DateProbePayloadSafetyTests(unittest.TestCase):
    def test_probe_rejects_impossible_numeric_ranges(self):
        import json
        from date_manager import _deserialize_probe
        base = {"page": 1, "relation": "target", "date_coverage": 1.0,
                "target_count": 3, "newer_count": 0, "older_count": 0}
        self.assertIsNotNone(_deserialize_probe(json.dumps(base)))
        for key, value in (("page", 0), ("page", 51), ("date_coverage", 1.2),
                           ("target_count", -1), ("max_page", 99), ("actual_page", 99)):
            payload = dict(base)
            payload[key] = value
            self.assertIsNone(_deserialize_probe(json.dumps(payload)), (key, value))

    def test_date_manager_binds_cached_probe_to_requested_page_statically(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "date_manager.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("item is not None and item.page == expected_page"), 2)
