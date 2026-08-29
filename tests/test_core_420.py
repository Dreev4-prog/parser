import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta

from early_winner import FeatureRow, _age_matched_rows
from organic_velocity import (
    ORGANIC_HIGH_BASELINE_VIEWS,
    apply_organic_measurement,
    demand_safe_metric,
)


@dataclass
class DummyListing:
    is_promoted: bool = False
    is_price_reduced: bool = False
    organic_baseline_views: int | None = None
    organic_baseline_at: datetime | None = None
    organic_history_status: str = "trusted_new"
    organic_verified_checkpoints: int = 0
    organic_last_checkpoint_at: datetime | None = None
    organic_last_checkpoint_views: int | None = None


def row(external_id: str, age_minutes: float) -> FeatureRow:
    return FeatureRow(
        external_id=external_id,
        category_key="test",
        identity_key=None,
        identity_label=None,
        identity_confidence=0,
        price_eur=100,
        views=100,
        age_minutes=age_minutes,
        title=external_id,
    )


class VerifiedOrganicVelocityTests(unittest.TestCase):
    def test_400_is_hard_first_observation_baseline(self):
        listing = DummyListing()
        t0 = datetime(2026, 8, 29, 9, 0, 0)
        transition = apply_organic_measurement(listing, ORGANIC_HIGH_BASELINE_VIEWS, t0)
        self.assertEqual(transition, "high_baseline_started")
        self.assertIsNone(demand_safe_metric(listing, 400, t0).views)

    def test_high_baseline_requires_two_spaced_checkpoints_and_uses_delta(self):
        listing = DummyListing()
        t0 = datetime(2026, 8, 29, 9, 0, 0)
        apply_organic_measurement(listing, 942, t0)
        self.assertEqual(apply_organic_measurement(listing, 1002, t0 + timedelta(minutes=30)), "high_checkpoint_1")
        self.assertIsNone(demand_safe_metric(listing, 1002, t0 + timedelta(minutes=30)).views)
        self.assertEqual(apply_organic_measurement(listing, 1077, t0 + timedelta(minutes=60)), "high_verified")
        metric = demand_safe_metric(listing, 1077, t0 + timedelta(minutes=60))
        self.assertEqual(metric.views, 135)
        self.assertEqual(metric.kind, "observed_delta")

    def test_399_first_observation_does_not_become_high_baseline_after_crossing_400(self):
        listing = DummyListing()
        t0 = datetime(2026, 8, 29, 9, 0, 0)
        self.assertEqual(apply_organic_measurement(listing, 399, t0), "trusted_total")
        self.assertEqual(apply_organic_measurement(listing, 520, t0 + timedelta(minutes=30)), "trusted_refresh")
        metric = demand_safe_metric(listing, 520, t0 + timedelta(minutes=30))
        self.assertEqual(metric.views, 520)
        self.assertEqual(metric.kind, "trusted_total")

    def test_too_early_high_baseline_recheck_does_not_count(self):
        listing = DummyListing()
        t0 = datetime(2026, 8, 29, 9, 0, 0)
        apply_organic_measurement(listing, 942, t0)
        self.assertEqual(apply_organic_measurement(listing, 960, t0 + timedelta(minutes=10)), "high_waiting_interval")
        self.assertEqual(listing.organic_verified_checkpoints, 0)

    def test_counter_rollback_never_certifies_high_baseline(self):
        listing = DummyListing()
        t0 = datetime(2026, 8, 29, 9, 0, 0)
        apply_organic_measurement(listing, 942, t0)
        self.assertEqual(apply_organic_measurement(listing, 900, t0 + timedelta(minutes=30)), "counter_rollback")
        self.assertEqual(listing.organic_verified_checkpoints, 0)
        self.assertIsNone(demand_safe_metric(listing, 900, t0 + timedelta(minutes=30)).views)


class AgeCohortTests(unittest.TestCase):
    def test_24_48h_rows_prefer_same_band(self):
        rows = [
            row("a", 1500), row("b", 1600), row("c", 1800),
            row("d", 2100), row("e", 2700), row("fresh", 120), row("too_old", 3000),
        ]
        matched = _age_matched_rows(rows[2], rows)
        ids = {x.external_id for x in matched}
        self.assertEqual(ids, {"a", "b", "c", "d", "e"})

    def test_0_3h_rows_do_not_mix_with_24_48h_when_band_is_large_enough(self):
        rows = [
            row("a", 30), row("b", 60), row("c", 90), row("d", 120), row("e", 170),
            row("old", 1800),
        ]
        matched = _age_matched_rows(rows[1], rows)
        self.assertNotIn("old", {x.external_id for x in matched})


if __name__ == "__main__":
    unittest.main()

class StrictAgeCohortTests(unittest.TestCase):
    def test_sparse_0_3h_cohort_does_not_borrow_24_48h_rows(self):
        from early_winner import FeatureRow, _age_matched_rows
        young = FeatureRow("y", "cat", None, None, None, 100, 30, 120.0)
        same_band = FeatureRow("y2", "cat", None, None, None, 110, 25, 170.0)
        old = FeatureRow("o", "cat", None, None, None, 120, 900, 30.0 * 60.0)
        rows = _age_matched_rows(young, [young, same_band, old])
        self.assertEqual({x.external_id for x in rows}, {"y", "y2"})
