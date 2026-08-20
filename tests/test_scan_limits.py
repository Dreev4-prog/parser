import unittest

from scan_selection import MAX_SELECTED_CATEGORIES, validate_scan_category_keys


class ScanLimitTests(unittest.TestCase):
    def test_two_categories_allowed(self):
        self.assertEqual(MAX_SELECTED_CATEGORIES, 2)
        self.assertEqual(
            validate_scan_category_keys(["a", "b"], {"a", "b", "c"}),
            ["a", "b"],
        )

    def test_three_categories_rejected(self):
        with self.assertRaises(ValueError):
            validate_scan_category_keys(["a", "b", "c"], {"a", "b", "c"})


if __name__ == "__main__":
    unittest.main()
