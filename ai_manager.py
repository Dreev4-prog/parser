from __future__ import annotations

import json
import os
import time
from typing import Any

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

REDIS_URL = os.getenv("REDIS_URL", "").strip()
AI_REDIS_PREFIX = os.getenv("AI_REDIS_PREFIX", "dtparser:ai").strip() or "dtparser:ai"
AI_HEARTBEAT_KEY = f"{AI_REDIS_PREFIX}:heartbeat"
AI_HEARTBEAT_STALE_SECONDS = max(8, min(120, int(os.getenv("AI_HEARTBEAT_STALE_SECONDS", "20"))))


class AIManager:
    def __init__(self) -> None:
        self.url = REDIS_URL
        self._redis: Any | None = None

    async def connect(self):
        if not self.url or Redis is None:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(
                self.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=4,
                socket_timeout=8,
                health_check_interval=20,
            )
            await self._redis.ping()
        return self._redis

    async def status(self) -> dict[str, Any]:
        base = {"enabled": bool(self.url), "alive": False, "error": None}
        if not self.url:
            return base
        try:
            redis = await self.connect()
            raw = await redis.get(AI_HEARTBEAT_KEY)
            if not raw:
                return base
            data = json.loads(raw)
            age = max(0.0, time.time() - float(data.get("ts", 0.0)))
            data["age_seconds"] = age
            base.update(data)
            base["alive"] = age <= AI_HEARTBEAT_STALE_SECONDS
            return base
        except Exception as exc:
            base["error"] = str(exc)[:300]
            return base

    async def close(self) -> None:
        redis = self._redis
        self._redis = None
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass


AI_MANAGER = AIManager()
