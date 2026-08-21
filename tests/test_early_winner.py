import unittest
from datetime import datetime

from early_winner import (
    FeatureRow,
    listing_age_minutes,
    score_initial_rows,
    select_candidates,
    update_dynamic_score,
)


class EarlyWinnerTests(unittest.TestCase):
    def test_berlin_clock_age_is_derived_from_snapshot_not_current_time(self):
        # 12:00 UTC = 14:00 Berlin in August. A card captured as Heute 13:00 is 60m old.
        age, exact = listing_age_minutes("Heute, 13:00", datetime(2026, 8, 21, 12, 0, 0))
        self.assertTrue(exact)
        self.assertAlmostEqual(age or 0, 60.0, delta=0.1)

    def test_date_only_cards_are_not_used_for_velocity_ai(self):
        age, exact = listing_age_minutes("19.08.2026", datetime(2026, 8, 21, 12, 0, 0))
        self.assertIsNone(age)
        self.assertFalse(exact)

    def test_fast_cheap_listing_scores_above_slow_expensive_peer(self):
        rows = [
            FeatureRow("fast", "cat", "portal", "Portal", 95, 120, 80, 60),
            FeatureRow("mid", "cat", "portal", "Portal", 95, 150, 40, 60),
            FeatureRow("slow", "cat", "portal", "Portal", 95, 180, 10, 60),
            FeatureRow("slow2", "cat", "portal", "Portal", 95, 170, 15, 60),
        ]
        scored = {x.external_id: x for x in score_initial_rows(rows, {"portal": (155.0, 20)})}
        self.assertGreater(scored["fast"].score, scored["slow"].score)
        self.assertGreater(scored["fast"].velocity_percentile, scored["slow"].velocity_percentile)
        self.assertGreater(scored["fast"].confidence, 70)


    def test_same_identity_cohort_is_preferred_when_available(self):
        rows = [
            FeatureRow("p1", "electronics", "portal", "Portal", 95, 120, 80, 60),
            FeatureRow("p2", "electronics", "portal", "Portal", 95, 130, 40, 60),
            FeatureRow("p3", "electronics", "portal", "Portal", 95, 140, 20, 60),
            FeatureRow("x1", "electronics", "iphone", "iPhone", 95, 500, 500, 60),
            FeatureRow("x2", "electronics", "iphone", "iPhone", 95, 510, 450, 60),
            FeatureRow("x3", "electronics", "iphone", "iPhone", 95, 520, 400, 60),
        ]
        scored = {x.external_id: x for x in score_initial_rows(rows, {})}
        self.assertEqual(scored["p1"].peer_count, 3)
        self.assertGreater(scored["p1"].peer_vph_median, scored["p3"].views_per_hour)
        self.assertTrue(any("сопоставимых объявления" in reason for reason in scored["p1"].reasons))


    def test_mass_market_family_is_penalized_against_hidden_gem(self):
        rows = []
        for i, views in enumerate([180, 160, 150, 140, 130, 120, 110, 100]):
            rows.append(FeatureRow(f"i{i}", "cat", "iphone", "iPhone", 95, 600, views, 120, title="Apple iPhone 15 128GB"))
        for i, views in enumerate([140, 120, 105, 90]):
            rows.append(FeatureRow(f"m{i}", "cat", None, None, 0, 140, views, 120, title="Makita DHP484Z Akku Bohrschrauber 18V"))
        for i, views in enumerate([10, 12, 15, 18, 20, 25, 30, 35]):
            rows.append(FeatureRow(f"o{i}", "cat", None, None, 0, 100, views, 120, title=f"Other Tool Model{i}"))
        from early_winner import opportunity_family_key
        stats = {
            "id:iphone": (600.0, 500),
            opportunity_family_key("Makita DHP484Z Akku Bohrschrauber 18V", "cat"): (150.0, 18),
        }
        scored = {x.external_id: x for x in score_initial_rows(rows, stats)}
        self.assertGreaterEqual(scored["m0"].score, 90)
        self.assertEqual(scored["m0"].opportunity_type, "hidden_gem")
        self.assertGreater(scored["i0"].mass_penalty, 25)
        self.assertLess(scored["i0"].score, scored["m0"].score)

    def test_rarity_without_demand_is_not_a_winner(self):
        rows = [
            FeatureRow("rare", "cat", None, None, 0, 100, 3, 120, title="Rare Foo ZX991 Device"),
            FeatureRow("normal1", "cat", None, None, 0, 100, 30, 120, title="Normal A100 Device"),
            FeatureRow("normal2", "cat", None, None, 0, 100, 25, 120, title="Normal B200 Device"),
            FeatureRow("normal3", "cat", None, None, 0, 100, 20, 120, title="Normal C300 Device"),
        ]
        from early_winner import opportunity_family_key
        stats = {opportunity_family_key("Rare Foo ZX991 Device", "cat"): (100.0, 1)}
        scored = {x.external_id: x for x in score_initial_rows(rows, stats)}
        self.assertLess(scored["rare"].score, 65)

    def test_candidate_diversity_caps_one_mass_family(self):
        rows = [
            FeatureRow(f"a{i}", "cat", "same", "Same", 95, 100, 200-i*5, 60, title="Same Product")
            for i in range(6)
        ] + [
            FeatureRow("b1", "cat", "other", "Other", 95, 100, 120, 60, title="Other Product")
        ]
        scores = score_initial_rows(rows, {"id:same": (100.0, 10), "id:other": (100.0, 10)})
        ids, controls = select_candidates(
            scores, {x.external_id: "cat" for x in rows}, score_floor=0,
            per_category=7, total_limit=7, control_per_category=0, max_per_cohort=2,
        )
        same_selected = [x for x in ids if x.startswith("a")]
        self.assertLessEqual(len(same_selected), 2)
        self.assertIn("b1", ids)

    def test_recent_interval_acceleration_strengthens_live_score(self):
        base = dict(
            initial_score=82,
            initial_views_per_hour=30,
            baseline_views=50,
            current_views=170,
            elapsed_hours=3,
            peer_vph_median=15,
            peer_vph_p85=40,
            target_hours=3,
        )
        lifetime_only = update_dynamic_score(**base)
        accelerated = update_dynamic_score(
            **base, previous_views=65, previous_elapsed_hours=1.0
        )
        self.assertGreaterEqual(accelerated.score, lifetime_only.score)
        self.assertGreater(accelerated.recent_views_per_hour, accelerated.observed_views_per_hour)
        self.assertTrue(any("последнего интервала" in reason for reason in accelerated.reasons))

    def test_candidate_budget_is_bounded_and_controls_are_explicit(self):
        rows = [FeatureRow(str(i), "cat", None, None, 0, None, i + 1, 60) for i in range(20)]
        scores = score_initial_rows(rows, {})
        ids, controls = select_candidates(
            scores,
            {str(i): "cat" for i in range(20)},
            score_floor=75,
            per_category=4,
            total_limit=4,
            control_per_category=2,
        )
        self.assertLessEqual(len([x for x in ids if x not in controls]), 4)
        self.assertLessEqual(len(controls), 2)
        self.assertTrue(controls.issubset(set(ids)))

    def test_strong_future_growth_can_confirm(self):
        result = update_dynamic_score(
            initial_score=91,
            initial_views_per_hour=40,
            baseline_views=40,
            current_views=220,
            elapsed_hours=3,
            peer_vph_median=15,
            peer_vph_p85=40,
            target_hours=3,
        )
        self.assertEqual(result.outcome, "confirmed")
        self.assertGreaterEqual(result.score, 88)

    def test_weak_six_hour_growth_rejects(self):
        result = update_dynamic_score(
            initial_score=82,
            initial_views_per_hour=30,
            baseline_views=50,
            current_views=70,
            elapsed_hours=6,
            peer_vph_median=12,
            peer_vph_p85=30,
            target_hours=6,
        )
        self.assertEqual(result.outcome, "rejected")


if __name__ == "__main__":
    unittest.main()
