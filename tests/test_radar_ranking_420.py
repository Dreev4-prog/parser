import unittest

from radar_ranking import classify_radar_signal, demand_gate_for_age, maturity_score_for_age


class Unified48HRadarRankingTests(unittest.TestCase):
    def test_demand_gates_are_age_aware(self):
        self.assertEqual(demand_gate_for_age(60), 30)
        self.assertEqual(demand_gate_for_age(180), 30)
        self.assertEqual(demand_gate_for_age(181), 40)
        self.assertEqual(demand_gate_for_age(360), 40)
        self.assertEqual(demand_gate_for_age(361), 60)
        self.assertEqual(demand_gate_for_age(720), 60)
        self.assertEqual(demand_gate_for_age(721), 80)
        self.assertEqual(demand_gate_for_age(1440), 80)
        self.assertEqual(demand_gate_for_age(1441), 100)
        self.assertEqual(demand_gate_for_age(2880), 100)

    def test_fifteen_views_cannot_be_hot_even_with_very_high_score(self):
        evidence = classify_radar_signal(
            dt_score=95, confidence=90, demand_views=15, age_minutes=120,
        )
        self.assertEqual(evidence.demand_gate, 30)
        self.assertEqual(evidence.status, "stable")
        self.assertTrue(evidence.admitted)
        self.assertLess(evidence.demand_ratio, 0.60)

    def test_today_listing_can_be_hot_after_real_demand_floor(self):
        evidence = classify_radar_signal(
            dt_score=80, confidence=60, demand_views=30, age_minutes=120,
        )
        self.assertEqual(evidence.status, "hot")
        self.assertTrue(evidence.admitted)
        self.assertGreaterEqual(evidence.demand_ratio, 1.0)

    def test_yesterday_listing_can_be_hot_in_same_radar(self):
        evidence = classify_radar_signal(
            dt_score=80, confidence=60, demand_views=100, age_minutes=30 * 60,
        )
        self.assertEqual(evidence.demand_gate, 100)
        self.assertEqual(evidence.status, "hot")
        self.assertTrue(evidence.admitted)

    def test_yesterday_listing_below_full_gate_is_strong_not_hot(self):
        evidence = classify_radar_signal(
            dt_score=80, confidence=60, demand_views=90, age_minutes=30 * 60,
        )
        self.assertEqual(evidence.status, "rising")
        self.assertTrue(evidence.admitted)
        self.assertLess(evidence.demand_ratio, 1.0)

    def test_thin_confidence_cannot_be_strong(self):
        evidence = classify_radar_signal(
            dt_score=90, confidence=22, demand_views=25, age_minutes=120,
        )
        self.assertEqual(evidence.status, "stable")
        self.assertTrue(evidence.admitted)

    def test_score_below_hot_threshold_never_becomes_hot(self):
        evidence = classify_radar_signal(
            dt_score=71, confidence=90, demand_views=500, age_minutes=60,
        )
        self.assertNotEqual(evidence.status, "hot")
        self.assertEqual(evidence.status, "rising")

    def test_too_old_signal_is_historical(self):
        evidence = classify_radar_signal(
            dt_score=99, confidence=99, demand_views=9999, age_minutes=48 * 60 + 1,
        )
        self.assertEqual(evidence.status, "historical")
        self.assertFalse(evidence.admitted)
        self.assertEqual(evidence.radar_rank, 0.0)

    def test_below_early_floor_has_zero_rank(self):
        evidence = classify_radar_signal(
            dt_score=90, confidence=90, demand_views=5, age_minutes=120,
        )
        self.assertEqual(evidence.status, "historical")
        self.assertFalse(evidence.admitted)
        self.assertEqual(evidence.radar_rank, 0.0)

    def test_maturity_only_orders_it_does_not_change_dt_score(self):
        young = classify_radar_signal(
            dt_score=80, confidence=60, demand_views=30, age_minutes=120,
        )
        mature = classify_radar_signal(
            dt_score=80, confidence=60, demand_views=100, age_minutes=30 * 60,
        )
        self.assertGreater(maturity_score_for_age(30 * 60), maturity_score_for_age(120))
        self.assertGreater(mature.radar_rank, young.radar_rank)
        # The public DT Score is an input to the rank layer and is not rewritten.
        expected_young = 0.70 * 80 + 0.20 * 60 + 0.10 * 35
        expected_mature = 0.70 * 80 + 0.20 * 60 + 0.10 * 100
        self.assertAlmostEqual(young.radar_rank, expected_young)
        self.assertAlmostEqual(mature.radar_rank, expected_mature)


if __name__ == "__main__":
    unittest.main()
