from __future__ import annotations

"""Dedicated browser-isolated parser worker for Railway.

Run exactly one local consumer per service/replica. Every claimed user scan gets one
independent Chromium context that is reused across that user's selected categories.
"""

import os

# These defaults must be set before importing bot/parser/traffic because their
# production configuration is intentionally read once at module import time.
os.environ.setdefault("SCAN_TRANSPORT", "browser")
os.environ.setdefault("PARSER_WORKER_CONCURRENCY", "1")
os.environ.setdefault("SHARE_ACTIVE_CATEGORY_SCANS", "0")
os.environ.setdefault("DIST_TRAFFIC_SHARED_COOLDOWN", "0")
os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "1")
os.environ.setdefault("TRAFFIC_BROWSER_CONCURRENCY", "1")

from parser_worker import main  # noqa: E402


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
