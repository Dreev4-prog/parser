import unittest

import distributed


class TrafficBucketTests(unittest.TestCase):
    def test_named_buckets_are_used_in_redis_keys(self):
        old_scan = distributed.DIST_TRAFFIC_SCAN_BUCKET
        old_global = distributed.DIST_TRAFFIC_GLOBAL_BUCKET
        try:
            distributed.DIST_TRAFFIC_SCAN_BUCKET = "date"
            distributed.DIST_TRAFFIC_GLOBAL_BUCKET = "search-fleet"
            coordinator = distributed.DistributedCoordinator()
            self.assertTrue(coordinator._traffic_kind_key("scan").endswith(":date"))
            self.assertTrue(coordinator._traffic_global_key().endswith(":global:search-fleet"))
        finally:
            distributed.DIST_TRAFFIC_SCAN_BUCKET = old_scan
            distributed.DIST_TRAFFIC_GLOBAL_BUCKET = old_global


if __name__ == "__main__":
    unittest.main()
