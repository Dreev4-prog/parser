from __future__ import annotations

"""HTTP-first stability worker for Railway.

Each replica processes one user scan at a time. Normal category pages are requested
through lightweight persistent HTTP first. Chromium starts only for compatibility/JS
failures, then hands its storage state to APIRequestContext and releases browser RAM.
Verified page checkpoints are reused by automatic partial recovery. Explicit 403/429
refusals are always honored and never bypassed by transport switching.
"""

import os

# Configure before parser/bot imports because production settings are read at import.
os.environ.setdefault("SCAN_TRANSPORT", "hybrid")
os.environ.setdefault("PARSER_WORKER_CONCURRENCY", "1")
os.environ.setdefault("SHARE_ACTIVE_CATEGORY_SCANS", "0")
# Keep refusal cooldown local to the affected replica so one session cannot freeze
# every user's progress. Cross-replica scan/global concurrency is still governed by Redis.
os.environ.setdefault("DIST_TRAFFIC_SHARED_COOLDOWN", "0")
os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "1")
os.environ.setdefault("TRAFFIC_BROWSER_CONCURRENCY", "1")
os.environ.setdefault("HYBRID_HTTP_FIRST", "1")
os.environ.setdefault("HYBRID_WATCHDOG_SECONDS", "15")
os.environ.setdefault("HYBRID_DIRECT_HTTP_RETRIES", "1")
os.environ.setdefault("SCAN_AUTO_RECOVERY_PASSES", "2")
os.environ.setdefault("SCAN_AUTO_RECOVERY_DELAY_SECONDS", "2")
os.environ.setdefault("SCAN_PAGE_CHECKPOINT_TTL_SECONDS", "900")

# v3.7.2: hybrid replicas keep independent foreground network lanes plus stability recovery.
# Older Railway Variables could leave DIST_TRAFFIC_SCAN_LIMIT at 2/3 and make
# some replicas look frozen. A dedicated hybrid profile deliberately wins over
# those generic legacy values while remaining tunable through HYBRID_* vars.
os.environ["DIST_TRAFFIC_SCAN_LIMIT"] = os.getenv("HYBRID_SCAN_LANES", "5")
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = os.getenv("HYBRID_GLOBAL_LANES", "8")

from parser_worker import main  # noqa: E402


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
