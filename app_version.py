from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """Return the release version shipped with this checkout.

    Worker heartbeats use the same VERSION file as the main release so admin
    telemetry cannot silently report an old historical worker version.
    """
    try:
        value = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()
        return value or "unknown"
    except Exception:
        return "unknown"


APP_VERSION = get_app_version()
