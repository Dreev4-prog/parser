import os
import unittest
from unittest.mock import patch

import service_launcher


class ServiceLauncherAITests(unittest.TestCase):
    def test_ai_worker_detected_by_railway_service_name(self):
        with patch.dict(os.environ, {"RAILWAY_SERVICE_NAME": "AI Worker", "DT_SERVICE_ROLE": ""}, clear=False):
            self.assertEqual(service_launcher._role(), "ai-worker")

    def test_ai_worker_detected_by_explicit_role(self):
        with patch.dict(os.environ, {"DT_SERVICE_ROLE": "ai-worker"}, clear=False):
            self.assertEqual(service_launcher._role(), "ai-worker")


if __name__ == "__main__":
    unittest.main()
