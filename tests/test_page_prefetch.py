import unittest

from page_manager import rolling_prefetch_range


class RollingPrefetchTests(unittest.TestCase):
    def test_initial_window_is_bounded(self):
        self.assertEqual(
            rolling_prefetch_range(8, 8, 50, window_pages=10, low_water_pages=4),
            (9, 18),
        )

    def test_no_top_up_while_window_is_warm(self):
        self.assertIsNone(
            rolling_prefetch_range(10, 18, 50, window_pages=10, low_water_pages=4)
        )

    def test_top_up_is_non_overlapping(self):
        self.assertEqual(
            rolling_prefetch_range(14, 18, 50, window_pages=10, low_water_pages=4),
            (19, 24),
        )

    def test_never_schedules_past_feed_limit(self):
        self.assertEqual(
            rolling_prefetch_range(47, 47, 50, window_pages=10, low_water_pages=4),
            (48, 50),
        )


if __name__ == "__main__":
    unittest.main()
