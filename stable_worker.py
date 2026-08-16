from __future__ import annotations

"""DT PARSER v3.8 Stable Scan Engine worker.

Kept as a dedicated entry point for Railway. It reuses the proven HTTP-first
hybrid transport, but category/date work is shared across users and verified
pages/date boundaries are persisted in PostgreSQL.
"""

import os

os.environ["SCAN_TRANSPORT"] = "hybrid"
os.environ["STABLE_SCAN_ENGINE"] = "1"
os.environ["PRIMARY_SCAN_INLINE_VIEWS"] = "0"
os.environ["SHARE_ACTIVE_CATEGORY_SCANS"] = "1"
os.environ.setdefault("PARSER_WORKER_CONCURRENCY", "1")
os.environ["DIST_TRAFFIC_SHARED_COOLDOWN"] = "0"
os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "1")
os.environ.setdefault("TRAFFIC_BROWSER_CONCURRENCY", "1")
os.environ.setdefault("HYBRID_HTTP_FIRST", "1")
os.environ.setdefault("HYBRID_WATCHDOG_SECONDS", "15")
os.environ.setdefault("HYBRID_DIRECT_HTTP_RETRIES", "1")
os.environ.setdefault("SCAN_AUTO_RECOVERY_PASSES", "3")
os.environ.setdefault("SCAN_AUTO_RECOVERY_DELAY_SECONDS", "2")
os.environ.setdefault("STABLE_PAGE_RETRIES", "3")
os.environ.setdefault("STABLE_PAGE_RETRY_SECONDS", "1.2")
os.environ.setdefault("STABLE_PAGE_CHECKPOINT_TTL_SECONDS", "300")
os.environ.setdefault("STABLE_DATE_INDEX_TTL_SECONDS", "900")

# Five independent foreground request lanes are enough for the initial rollout.
os.environ["DIST_TRAFFIC_SCAN_LIMIT"] = os.getenv("STABLE_SCAN_LANES", "5")
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = os.getenv("STABLE_GLOBAL_LANES", "8")

from parser_worker import main  # noqa: E402


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
