from __future__ import annotations

"""DT PARSER v4.0 Railway Browser Fleet worker.

One Railway replica owns one long-lived Chromium process. Each local worker lane
gets an isolated BrowserContext inside that process. Scaling replicas therefore
adds real CPU/RAM capacity while avoiding the startup/RAM cost of a full Chromium
process per user scan.
"""

import os


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(lo, min(hi, value))


# Two isolated contexts per replica is the production default. On Hobby, six
# replicas therefore expose up to 12 browser lanes while still keeping one
# Chromium process per container. Raise to 3 only after watching Railway RAM.
LOCAL_CONTEXTS = _int_env("FLEET_CONTEXTS_PER_REPLICA", 2, 1, 3)
TOTAL_SCAN_LANES = _int_env("FLEET_TOTAL_SCAN_LANES", 8, 1, 24)
TOTAL_GLOBAL_LANES = _int_env("FLEET_TOTAL_GLOBAL_LANES", 10, 2, 32)

os.environ["SCAN_TRANSPORT"] = "browser"
os.environ["STABLE_SCAN_ENGINE"] = "1"
os.environ["SHARED_BROWSER_RUNTIME"] = "1"
os.environ["PRIMARY_SCAN_INLINE_VIEWS"] = "0"
# Every active user gets an independent BrowserContext. Completed-result caches
# still work, but a slow in-flight user cannot make another user subscribe to it.
os.environ["SHARE_ACTIVE_CATEGORY_SCANS"] = "0"
os.environ["PARSER_WORKER_CONCURRENCY"] = str(LOCAL_CONTEXTS)

# Local process capacity matches the number of contexts. Background views remain
# separate in views-worker and cannot fill the interactive browser pool.
os.environ["TRAFFIC_SCAN_CONCURRENCY"] = str(LOCAL_CONTEXTS)
os.environ["TRAFFIC_BROWSER_CONCURRENCY"] = str(LOCAL_CONTEXTS)
os.environ["TRAFFIC_GLOBAL_CONCURRENCY"] = str(max(LOCAL_CONTEXTS + 1, 3))
os.environ.setdefault("TRAFFIC_SCAN_MIN_INTERVAL_SECONDS", "0.35")
os.environ.setdefault("TRAFFIC_BROWSER_MIN_INTERVAL_SECONDS", "0.35")
os.environ.setdefault("TRAFFIC_BACKGROUND_VIEWS_DURING_SCANS", "0")

# Redis remains the global governor across all replicas. A cluster-wide refusal
# should slow the fleet instead of letting every Chromium keep retrying at once.
os.environ["DIST_TRAFFIC_SCAN_LIMIT"] = str(TOTAL_SCAN_LANES)
os.environ["DIST_TRAFFIC_BROWSER_LIMIT"] = str(TOTAL_SCAN_LANES)
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = str(TOTAL_GLOBAL_LANES)
os.environ["DIST_TRAFFIC_SHARED_COOLDOWN"] = "1"

# Browser mode should fail/recover in bounded time rather than leaving one user on
# an endless date-search screen. The Stable Engine persists verified pages.
os.environ.setdefault("BROWSER_SCAN_NAV_TIMEOUT_MS", "25000")
os.environ.setdefault("BROWSER_SCAN_ACCESS_MAX_WAIT_SECONDS", "60")
os.environ.setdefault("SCAN_AUTO_RECOVERY_PASSES", "2")
os.environ.setdefault("STABLE_PAGE_RETRIES", "2")
os.environ.setdefault("STABLE_PAGE_CHECKPOINT_TTL_SECONDS", "900")
os.environ.setdefault("STABLE_DATE_INDEX_TTL_SECONDS", "1800")

from parser_worker import main  # noqa: E402


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
