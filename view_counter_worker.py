from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

# This process owns only public view counters. Configure its process-wide traffic
# manager BEFORE parser.py / traffic.py are imported.
VIEW_POOL = max(2, min(24, int(os.getenv("VIEW_WORKER_VIEW_POOL_SIZE", "12"))))
BROWSER_POOL = max(1, min(4, int(os.getenv("VIEW_WORKER_BROWSER_POOL_SIZE", "2"))))
VIEW_INTERVAL = max(0.0, min(2.0, float(os.getenv("VIEW_WORKER_VIEW_MIN_INTERVAL_SECONDS", "0.05"))))
os.environ["TRAFFIC_VIEW_CONCURRENCY"] = str(VIEW_POOL)
os.environ["TRAFFIC_BROWSER_CONCURRENCY"] = str(BROWSER_POOL)
os.environ["TRAFFIC_GLOBAL_CONCURRENCY"] = str(max(VIEW_POOL + BROWSER_POOL, 14))
os.environ["TRAFFIC_VIEW_MIN_INTERVAL_SECONDS"] = str(VIEW_INTERVAL)
os.environ["TRAFFIC_BACKGROUND_VIEWS_DURING_SCANS"] = "0"
os.environ["ACCURATE_VIEW_HTTP_CONCURRENCY"] = str(min(24, max(12, VIEW_POOL * 2)))
os.environ["ACCURATE_VIEW_BROWSER_CONCURRENCY"] = str(BROWSER_POOL)
# One dedicated worker, one long-lived Chromium process for rare fallback pages.
os.environ.setdefault("SHARED_BROWSER_RUNTIME", "1")

try:
    from redis.asyncio import Redis  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("redis package is required for view worker") from exc

from parser import KleinanzeigenParser, shutdown_shared_browser_runtime
from view_manager import (
    REDIS_URL, VIEW_GROUP, VIEW_HEARTBEAT_KEY, VIEW_JOB_TTL_SECONDS,
    VIEW_REDIS_PREFIX, VIEW_RESULT_TTL_SECONDS, VIEW_STREAM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-view-counter-worker")

ROUND_SIZE = max(VIEW_POOL, min(200, int(os.getenv("VIEW_WORKER_ROUND_SIZE", "48"))))
MAX_ACTIVE_JOBS = max(1, min(16, int(os.getenv("VIEW_WORKER_MAX_ACTIVE_JOBS", "8"))))
BLOCK_MS = max(50, min(2000, int(os.getenv("VIEW_WORKER_QUEUE_BLOCK_MS", "250"))))
HEARTBEAT_SECONDS = max(1.0, min(10.0, float(os.getenv("VIEW_WORKER_HEARTBEAT_SECONDS", "3"))))
RECLAIM_IDLE_MS = max(15_000, min(10 * 60_000, int(os.getenv("VIEW_WORKER_RECLAIM_IDLE_MS", "60000"))))


@dataclass
class ActiveJob:
    job_id: str
    message_id: str
    urls: deque[str]
    total: int
    priority: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)


class ViewCounterWorker:
    def __init__(self) -> None:
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL is not set")
        self.redis = Redis.from_url(
            REDIS_URL, encoding="utf-8", decode_responses=True,
            socket_connect_timeout=5, socket_timeout=10, health_check_interval=20,
        )
        self.consumer = f"view-{socket.gethostname()}-{os.getpid()}"
        self.active: OrderedDict[str, ActiveJob] = OrderedDict()
        self.parser = KleinanzeigenParser()
        self._group_ready = False
        self._last_hb = 0.0

    def payload_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:payload"

    def progress_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:progress"

    def result_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:result"

    def cancel_key(self, job_id: str) -> str:
        return f"{VIEW_REDIS_PREFIX}:job:{job_id}:cancel"

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.redis.xgroup_create(VIEW_STREAM, VIEW_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc).upper():
                raise
        self._group_ready = True

    async def heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_hb < HEARTBEAT_SECONDS:
            return
        self._last_hb = now
        payload = {
            "ts": time.time(), "consumer": self.consumer,
            "view_pool": VIEW_POOL, "browser_pool": BROWSER_POOL,
            "active_jobs": len(self.active), "round_size": ROUND_SIZE,
        }
        await self.redis.set(VIEW_HEARTBEAT_KEY, json.dumps(payload), ex=max(10, int(HEARTBEAT_SECONDS * 4)))

    async def _load_message(self, msg_id: str, fields: dict[str, Any]) -> bool:
        job_id = str((fields or {}).get("job_id") or "")
        if not job_id or job_id in self.active:
            if not job_id:
                await self.redis.xack(VIEW_STREAM, VIEW_GROUP, msg_id)
            return False
        raw = await self.redis.get(self.payload_key(job_id))
        if not raw:
            await self.redis.xack(VIEW_STREAM, VIEW_GROUP, msg_id)
            return False
        try:
            payload = json.loads(raw)
            urls = [str(x) for x in payload.get("urls", []) if isinstance(x, str) and x]
        except Exception:
            urls = []
            payload = {}
        if not urls:
            await self.redis.xack(VIEW_STREAM, VIEW_GROUP, msg_id)
            return False
        job = ActiveJob(
            job_id=job_id, message_id=str(msg_id), urls=deque(dict.fromkeys(urls)),
            total=len(dict.fromkeys(urls)), priority=str(payload.get("priority") or "scan_inline"),
        )
        self.active[job_id] = job
        await self.redis.hset(self.progress_key(job_id), mapping={
            "done": 0, "total": job.total, "state": "running", "updated_at": time.time(),
        })
        await self.redis.expire(self.progress_key(job_id), VIEW_JOB_TTL_SECONDS)
        log.info("View job admitted job=%s total=%s active_jobs=%s", job_id[:10], job.total, len(self.active))
        return True

    async def admit_jobs(self) -> None:
        await self.ensure_group()
        while len(self.active) < MAX_ACTIVE_JOBS:
            loaded = False
            # First reclaim an abandoned pending job from a crashed worker.
            try:
                claimed = await self.redis.xautoclaim(
                    VIEW_STREAM, VIEW_GROUP, self.consumer,
                    min_idle_time=RECLAIM_IDLE_MS, start_id="0-0", count=1,
                )
                messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
                if messages:
                    msg_id, fields = messages[0]
                    loaded = await self._load_message(str(msg_id), fields or {})
            except Exception:
                pass
            if loaded:
                continue
            rows = await self.redis.xreadgroup(
                VIEW_GROUP, self.consumer, {VIEW_STREAM: ">"}, count=1,
                block=BLOCK_MS if not self.active else 1,
            )
            if not rows:
                break
            _stream, messages = rows[0]
            if not messages:
                break
            msg_id, fields = messages[0]
            await self._load_message(str(msg_id), fields or {})

    async def finish_job(self, job: ActiveJob) -> None:
        payload = {"job_id": job.job_id, "results": job.results, "completed_at": time.time()}
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(self.result_key(job.job_id), json.dumps(payload, ensure_ascii=False), ex=VIEW_RESULT_TTL_SECONDS)
        pipe.hset(self.progress_key(job.job_id), mapping={
            "done": job.total, "total": job.total, "state": "done", "updated_at": time.time(),
        })
        pipe.expire(self.progress_key(job.job_id), VIEW_RESULT_TTL_SECONDS)
        pipe.xack(VIEW_STREAM, VIEW_GROUP, job.message_id)
        pipe.xdel(VIEW_STREAM, job.message_id)
        await pipe.execute()
        elapsed = max(0.001, time.monotonic() - job.started_at)
        good = sum(1 for item in job.results.values() if item.get("views") is not None)
        log.info(
            "View job complete job=%s exact=%s/%s elapsed=%.1fs rate=%.2f/s",
            job.job_id[:10], good, job.total, elapsed, job.total / elapsed,
        )
        self.active.pop(job.job_id, None)

    async def cancel_job(self, job: ActiveJob) -> None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.hset(self.progress_key(job.job_id), mapping={
            "state": "cancelled", "updated_at": time.time(),
        })
        pipe.expire(self.progress_key(job.job_id), VIEW_RESULT_TTL_SECONDS)
        pipe.xack(VIEW_STREAM, VIEW_GROUP, job.message_id)
        pipe.xdel(VIEW_STREAM, job.message_id)
        await pipe.execute()
        self.active.pop(job.job_id, None)
        log.info("View job cancelled job=%s", job.job_id[:10])

    def fair_round(self) -> list[tuple[ActiveJob, str]]:
        jobs = [job for job in self.active.values() if job.urls]
        if not jobs:
            return []
        selected: list[tuple[ActiveJob, str]] = []
        # Interleave one URL per active scan at a time. One scan can use the whole
        # worker when alone; four scans naturally share the same global 12-slot lane.
        while len(selected) < ROUND_SIZE and any(job.urls for job in jobs):
            for job in jobs:
                if len(selected) >= ROUND_SIZE:
                    break
                if job.urls:
                    selected.append((job, job.urls.popleft()))
        return selected

    @staticmethod
    def serialize_result(vr) -> dict[str, Any]:
        return {
            "views": int(vr.views) if vr.views is not None else None,
            # raw_text is diagnostics only; keep it bounded so Redis never becomes
            # a large payload store for thousands of ads.
            "raw_text": (vr.raw_text[:400] if isinstance(vr.raw_text, str) else None),
            "source": str(vr.source or "remote:unknown"),
            "final_url": vr.final_url,
            "page_title": (vr.page_title[:200] if isinstance(vr.page_title, str) else None),
            "error": (vr.error[:400] if isinstance(vr.error, str) else None),
        }

    async def process_round(self) -> None:
        selected = self.fair_round()
        if not selected:
            for job in list(self.active.values()):
                if not job.urls and len(job.results) >= job.total:
                    await self.finish_job(job)
            return

        urls = [url for _, url in selected]
        # Combined batch preserves the exact v4.3.8 parser/view algorithm. Only
        # scheduling changed: the dedicated service feeds it a fair mix of users.
        try:
            results = await self.parser.fetch_public_view_counts(
                urls,
                concurrency=VIEW_POOL,
                progress_cb=None,
                traffic_priority="normal",
                browser_fallback=True,
                direct_http_only=False,
                accurate=True,
            )
        except Exception:
            # Nothing from a failed round is silently lost. Put every URL back
            # into its original job and retry after the worker-level backoff.
            for job, url in reversed(selected):
                job.urls.appendleft(url)
            raise
        touched: set[str] = set()
        for job, url in selected:
            touched.add(job.job_id)
            vr = results.get(url)
            if vr is None:
                job.results[url] = {
                    "views": None, "raw_text": None, "source": "remote:missing-result",
                    "final_url": None, "page_title": None, "error": "worker returned no result",
                }
            else:
                job.results[url] = self.serialize_result(vr)

        for job_id in touched:
            job = self.active.get(job_id)
            if job is None:
                continue
            done = len(job.results)
            await self.redis.hset(self.progress_key(job_id), mapping={
                "done": done, "total": job.total, "state": "running", "updated_at": time.time(),
            })
            await self.redis.expire(self.progress_key(job_id), VIEW_JOB_TTL_SECONDS)

        for job in list(self.active.values()):
            if await self.redis.exists(self.cancel_key(job.job_id)):
                await self.cancel_job(job)
            elif not job.urls and len(job.results) >= job.total:
                await self.finish_job(job)

    async def run(self) -> None:
        await self.redis.ping()
        await self.ensure_group()
        await self.heartbeat(force=True)
        log.info(
            "DT PARSER dedicated view worker online | consumer=%s view_pool=%s browser_pool=%s round=%s max_jobs=%s",
            self.consumer, VIEW_POOL, BROWSER_POOL, ROUND_SIZE, MAX_ACTIVE_JOBS,
        )
        try:
            while True:
                await self.heartbeat()
                await self.admit_jobs()
                if self.active:
                    try:
                        await self.process_round()
                    except Exception:
                        # Keep the worker alive. Return the selected URLs to normal
                        # parser handling on the next round only when possible; an
                        # unexpected process-level failure is logged prominently.
                        log.exception("Dedicated view round failed")
                        await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(0.05)
        finally:
            try:
                await self.parser.close()
            finally:
                await shutdown_shared_browser_runtime()
                await self.redis.aclose()


async def main() -> None:
    worker = ViewCounterWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
