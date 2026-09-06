from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import httpx
from sqlalchemy import case, func, select, update

from app_version import APP_VERSION
from db import SessionLocal
from models import VintedMetricHistory, VintedRadarWatch, VintedScan, VintedScanCategory, VintedScanItem
from vinted_probe import DEFAULT_BASE_URL, DEFAULT_USER_AGENT
from vinted_session_store import load_vinted_session_json

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

log = logging.getLogger("vinted-lab")

VINTED_REDIS_PREFIX = (os.getenv("VINTED_REDIS_PREFIX") or "dtparser:vintedlab").strip() or "dtparser:vintedlab"
REDIS_URL = os.getenv("REDIS_URL", "").strip()
SCAN_STREAM = f"{VINTED_REDIS_PREFIX}:scan_tasks"
SCAN_GROUP = f"{VINTED_REDIS_PREFIX}:scan_workers"
METRICS_STREAM = f"{VINTED_REDIS_PREFIX}:metric_tasks"
METRICS_GROUP = f"{VINTED_REDIS_PREFIX}:metrics_workers"
HEARTBEAT_TTL_SECONDS = 25
PENDING_RECLAIM_IDLE_MS = 60_000

# v4.23.10: targeted Vinted Radar follow-up checkpoints. Full-market scans keep
# discovering new inventory, while the Metrics fleet revisits only promising items.
VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES = (30, 60, 120, 180)
VINTED_RADAR_FOLLOWUP_RETRY_MINUTES = max(5, min(30, int(os.getenv("VINTED_RADAR_FOLLOWUP_RETRY_MINUTES", "10") or 10)))
VINTED_RADAR_FOLLOWUP_RETRIES_PER_STEP = max(0, min(3, int(os.getenv("VINTED_RADAR_FOLLOWUP_RETRIES_PER_STEP", "1") or 1)))

FALLBACK_CATALOGS: list[dict[str, Any]] = [
    {
        "id": 1904,
        "title": "Damen",
        "catalogs": [
            {"id": 4, "title": "Kleidung", "catalogs": []},
        ],
    },
    {
        "id": 5,
        "title": "Herren",
        "catalogs": [
            {"id": 2050, "title": "Kleidung", "catalogs": []},
        ],
    },
    {"id": 1193, "title": "Kinder", "catalogs": []},
]

_catalog_cache: tuple[float, list[dict[str, Any]], str] | None = None
_catalog_lock = asyncio.Lock()

# v4.23.2: full-market Radar must not become a no-op when Vinted temporarily
# hides `/api/v2/catalog/initializers` from Railway.  A current public DE catalog
# snapshot is used only as metadata fallback; live Vinted data always has priority.
VINTED_CATALOG_SNAPSHOT_URL = (
    os.getenv("VINTED_CATALOG_SNAPSHOT_URL")
    or "https://raw.githubusercontent.com/JakobAIOdev/vinted-dataset/main/output/de/groups.json"
).strip()
VINTED_CATALOG_SNAPSHOT_TIMEOUT_SECONDS = max(3.0, min(20.0, float(os.getenv("VINTED_CATALOG_SNAPSHOT_TIMEOUT_SECONDS", "8") or 8)))


def utcnow() -> datetime:
    return datetime.utcnow()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default



def _balanced_json_slice(source: str, start: int) -> str | None:
    if start < 0 or start >= len(source) or source[start] not in "[{":
        return None
    opener = source[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(source)):
        ch = source[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return source[start:idx + 1]
    return None


def _json_array_after_marker(source: str, marker: str) -> list[Any] | None:
    pos = source.find(marker)
    if pos < 0:
        return None
    start = source.find("[", pos + len(marker))
    if start < 0:
        return None
    blob = _balanced_json_slice(source, start)
    if not blob:
        return None
    try:
        parsed = json.loads(blob)
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def _extract_catalog_tree_from_html(html_text: str) -> list[Any] | None:
    """Read current nested catalog metadata from Vinted's own Next.js payload.

    This is metadata only.  It does not open item pages or collect demand evidence.
    The parser mirrors the normal page payload structure and falls back safely if
    Vinted changes it.
    """
    if not html_text:
        return None
    sources = [html_text]
    # Current Vinted serialises much of the catalog tree inside Next.js Flight chunks.
    pattern = re.compile(r'self\.__next_f\.push\(\[\d+\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)', re.S)
    for match in pattern.finditer(html_text):
        try:
            decoded = json.loads(match.group(1))
        except Exception:
            continue
        if isinstance(decoded, str) and decoded:
            sources.append(decoded)
    markers = ('"catalogTree":', '"dtos":{"catalogs":', '\\"catalogTree\\":', '\\"dtos\\":{\\"catalogs\\":')
    for source in sources:
        for marker in markers:
            tree = _json_array_after_marker(source, marker)
            if tree:
                return tree
    return None


def _normalize_catalog_node(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    catalog_id = _int(raw.get("id"), 0)
    if catalog_id <= 0:
        return None
    title = str(raw.get("title") or raw.get("name") or f"Catalog {catalog_id}").strip()
    children_raw = raw.get("catalogs")
    if not isinstance(children_raw, list):
        children_raw = raw.get("children") if isinstance(raw.get("children"), list) else []
    children: list[dict[str, Any]] = []
    for child in children_raw:
        normalized = _normalize_catalog_node(child)
        if normalized is not None:
            children.append(normalized)
    return {"id": catalog_id, "title": title, "catalogs": children}


def _normalize_catalog_snapshot(payload: Any) -> list[dict[str, Any]]:
    """Convert the public DE dataset shape (`children` mapping) to our catalog tree.

    This fallback is metadata only.  It never contains demand metrics and is accepted
    only after structural validation by the Radar resolver.
    """
    if not isinstance(payload, dict):
        return []

    def convert(label: str, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        catalog_id = _int(raw.get("id"), 0)
        if catalog_id <= 0:
            return None
        title = str(raw.get("title") or raw.get("name") or label or f"Catalog {catalog_id}").strip()
        children_raw = raw.get("children")
        children: list[dict[str, Any]] = []
        if isinstance(children_raw, dict):
            for child_label, child_raw in children_raw.items():
                child = convert(str(child_label), child_raw)
                if child is not None:
                    children.append(child)
        elif isinstance(children_raw, list):
            for child_raw in children_raw:
                child = convert("", child_raw)
                if child is not None:
                    children.append(child)
        return {"id": catalog_id, "title": title, "catalogs": children}

    roots: list[dict[str, Any]] = []
    for label, raw in payload.items():
        root = convert(str(label), raw)
        if root is not None:
            roots.append(root)
    return roots


async def _fetch_catalog_snapshot() -> list[dict[str, Any]]:
    if not VINTED_CATALOG_SNAPSHOT_URL.startswith(("https://", "http://")):
        return []
    try:
        headers = {"Accept": "application/json", "User-Agent": DEFAULT_USER_AGENT}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=VINTED_CATALOG_SNAPSHOT_TIMEOUT_SECONDS) as client:
            response = await client.get(VINTED_CATALOG_SNAPSHOT_URL)
            if response.status_code != 200:
                log.warning("Vinted catalog snapshot unavailable status=%s", response.status_code)
                return []
            roots = _normalize_catalog_snapshot(response.json())
            if roots:
                return roots
    except Exception as exc:
        log.warning("Vinted catalog snapshot fallback failed: %s", type(exc).__name__)
    return []


async def fetch_catalog_tree(*, force: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Fetch current Vinted DE category roots, with a safe local fallback.

    Category metadata is configuration, not demand evidence. Failure here must never
    block already-configured scans; the admin can still test the broad fallback roots.
    """
    global _catalog_cache
    previous_cache = _catalog_cache
    now = time.monotonic()
    if not force and _catalog_cache and now - _catalog_cache[0] < 1800:
        return _catalog_cache[1], _catalog_cache[2]

    async with _catalog_lock:
        now = time.monotonic()
        if not force and _catalog_cache and now - _catalog_cache[0] < 1800:
            return _catalog_cache[1], _catalog_cache[2]
        base = (os.getenv("VINTED_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": f"{base}/catalog",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=12.0) as client:
                # If the admin captured a normal logged-in Vinted browser session, reuse only
                # first-party cookies for category metadata. This often exposes the complete
                # current tree when anonymous initializers are restricted. Missing/invalid
                # session data still falls back safely and never blocks scanning.
                raw_session = os.getenv("VINTED_SESSION_JSON", "").strip() or await load_vinted_session_json()
                if raw_session:
                    try:
                        session_payload = json.loads(raw_session)
                        cookies = session_payload.get("cookies") if isinstance(session_payload, dict) else session_payload
                        host = base.split("//", 1)[-1].split("/", 1)[0]
                        if isinstance(cookies, dict):
                            for name, value in cookies.items():
                                if isinstance(name, str) and value is not None:
                                    client.cookies.set(name, str(value), domain=host, path="/")
                        elif isinstance(cookies, list):
                            for entry in cookies:
                                if not isinstance(entry, dict):
                                    continue
                                name = str(entry.get("name") or "").strip()
                                value = entry.get("value")
                                domain = str(entry.get("domain") or host).strip() or host
                                if name and value is not None and domain.lstrip(".").endswith("vinted.de"):
                                    client.cookies.set(name, str(value), domain=domain, path=str(entry.get("path") or "/"))
                    except Exception:
                        pass
                bootstrap_response = await client.get(f"{base}/catalog")
                if bootstrap_response.status_code == 200:
                    raw_tree = _extract_catalog_tree_from_html(bootstrap_response.text)
                    if isinstance(raw_tree, list):
                        roots = [node for raw in raw_tree if (node := _normalize_catalog_node(raw))]
                        if roots:
                            _catalog_cache = (time.monotonic(), roots, "live-page")
                            return roots, "live-page"
                response = await client.get(
                    f"{base}/api/v2/catalog/initializers",
                    params={"page": 1, "time": time.time()},
                )
                if response.status_code == 200:
                    payload = response.json()
                    raw_catalogs: Any = None
                    if isinstance(payload, dict):
                        dtos = payload.get("dtos")
                        if isinstance(dtos, dict):
                            raw_catalogs = dtos.get("catalogs")
                        if raw_catalogs is None:
                            raw_catalogs = payload.get("catalogs")
                    if isinstance(raw_catalogs, list):
                        roots = [node for raw in raw_catalogs if (node := _normalize_catalog_node(raw))]
                        if roots:
                            _catalog_cache = (time.monotonic(), roots, "live")
                            return roots, "live"
        except Exception as exc:
            log.debug("Vinted catalog metadata fallback: %s", exc)

        # A forced refresh must not destroy a previously validated complete tree.
        if previous_cache and str(previous_cache[2]).startswith(("live", "snapshot")):
            roots = json.loads(json.dumps(previous_cache[1]))
            _catalog_cache = (time.monotonic(), roots, str(previous_cache[2]))
            return roots, str(previous_cache[2])

        # Public metadata snapshot fallback.  This is deliberately preferred over the
        # tiny local demo tree because Radar promises an all-category market pass.
        snapshot_roots = await _fetch_catalog_snapshot()
        if snapshot_roots:
            _catalog_cache = (time.monotonic(), snapshot_roots, "snapshot-de")
            return snapshot_roots, "snapshot-de"

        roots = json.loads(json.dumps(FALLBACK_CATALOGS))
        _catalog_cache = (time.monotonic(), roots, "fallback")
        return roots, "fallback"


def flatten_catalog_tree(roots: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}

    def walk(node: dict[str, Any], parent_id: int | None = None, depth: int = 0) -> None:
        catalog_id = _int(node.get("id"), 0)
        if catalog_id <= 0:
            return
        out[catalog_id] = {
            "id": catalog_id,
            "title": str(node.get("title") or f"Catalog {catalog_id}"),
            "parent_id": parent_id,
            "depth": depth,
            "catalogs": list(node.get("catalogs") or []),
        }
        for child in list(node.get("catalogs") or []):
            if isinstance(child, dict):
                walk(child, catalog_id, depth + 1)

    for root in roots:
        walk(root)
    return out


def leaf_catalogs_from_tree(roots: Iterable[dict[str, Any]]) -> list[tuple[int, str]]:
    """Return every terminal market category once, with a readable breadcrumb.

    Radar uses this complete leaf set to validate that the market tree is structurally
    complete before building its bounded non-overlapping scan segments.
    """
    leaves: list[tuple[int, str]] = []
    seen: set[int] = set()

    def walk(node: dict[str, Any], path: list[str]) -> None:
        catalog_id = _int(node.get("id"), 0)
        if catalog_id <= 0:
            return
        title = str(node.get("title") or f"Catalog {catalog_id}").strip()
        current_path = [*path, title]
        children = [child for child in list(node.get("catalogs") or []) if isinstance(child, dict) and _int(child.get("id"), 0) > 0]
        if children:
            for child in children:
                walk(child, current_path)
            return
        if catalog_id in seen:
            return
        seen.add(catalog_id)
        leaves.append((catalog_id, " › ".join(current_path)[-255:]))

    for root in roots:
        if isinstance(root, dict):
            walk(root, [])
    return leaves


def balanced_catalog_segments_from_tree(
    roots: Iterable[dict[str, Any]], *, target_segments: int = 120, max_segments: int = 150,
) -> list[tuple[int, str]]:
    """Partition the complete market tree into non-overlapping scan segments.

    Vinted's DE tree contains thousands of terminal catalog ids. Scanning every leaf at
    15 pages would create tens of thousands of requests per Radar round. Instead we start
    with the market roots and repeatedly split the currently broadest subtree until the
    requested segment budget is reached. Because a node is replaced by its children, a
    parent and child are never scanned together and every terminal category remains covered
    by exactly one selected subtree.
    """
    target_segments = max(1, int(target_segments or 1))
    max_segments = max(target_segments, int(max_segments or target_segments))

    def build(node: dict[str, Any], path: list[str], depth: int) -> dict[str, Any] | None:
        catalog_id = _int(node.get("id"), 0)
        if catalog_id <= 0:
            return None
        title = str(node.get("title") or f"Catalog {catalog_id}").strip()
        current_path = [*path, title]
        children_meta: list[dict[str, Any]] = []
        for child in list(node.get("catalogs") or []):
            if not isinstance(child, dict):
                continue
            child_meta = build(child, current_path, depth + 1)
            if child_meta is not None:
                children_meta.append(child_meta)
        leaf_count = sum(int(child["leaf_count"]) for child in children_meta) if children_meta else 1
        meta = {
            "id": catalog_id,
            "name": " › ".join(current_path)[-255:],
            "depth": depth,
            "children": children_meta,
            "leaf_count": max(1, int(leaf_count)),
        }
        return meta

    frontier: list[dict[str, Any]] = []
    seen_roots: set[int] = set()
    for raw_root in roots:
        if not isinstance(raw_root, dict):
            continue
        meta = build(raw_root, [], 0)
        if meta is None or int(meta["id"]) in seen_roots:
            continue
        seen_roots.add(int(meta["id"]))
        frontier.append(meta)

    if not frontier:
        return []

    # Split the largest remaining market subtree first. This makes the mixed-depth
    # partition much more balanced than a fixed depth while keeping the queue bounded.
    while len(frontier) < target_segments:
        candidates: list[tuple[int, int, int, dict[str, Any]]] = []
        for entry in frontier:
            children = list(entry.get("children") or [])
            if not children:
                continue
            projected = len(frontier) - 1 + len(children)
            if projected > max_segments:
                continue
            candidates.append((
                int(entry.get("leaf_count") or 1),
                -int(entry.get("depth") or 0),
                len(children),
                entry,
            ))
        if not candidates:
            break
        _leaf_span, _neg_depth, _child_count, chosen = max(candidates, key=lambda row: (row[0], row[1], row[2], -int(row[3].get("id") or 0)))
        idx = frontier.index(chosen)
        frontier[idx:idx + 1] = list(chosen.get("children") or [])

    result: list[tuple[int, str]] = []
    seen: set[int] = set()
    for entry in frontier:
        cid = int(entry.get("id") or 0)
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        result.append((cid, str(entry.get("name") or f"Catalog {cid}")[:255]))
    return result


class VintedQueueUnavailable(RuntimeError):
    pass


class VintedLabQueue:
    def __init__(self) -> None:
        self.url = REDIS_URL
        self._redis: Any | None = None
        self._lock = asyncio.Lock()
        self._groups_ready = False

    @property
    def enabled(self) -> bool:
        return bool(self.url and Redis is not None)

    async def connect(self) -> Any:
        if not self.enabled:
            raise VintedQueueUnavailable("REDIS_URL is required for Vinted Lab workers")
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=10,
                    health_check_interval=30,
                )
                await self._redis.ping()
        return self._redis

    async def close(self) -> None:
        redis = self._redis
        self._redis = None
        self._groups_ready = False
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass

    async def ensure_groups(self) -> None:
        if self._groups_ready:
            return
        redis = await self.connect()
        for stream, group in ((SCAN_STREAM, SCAN_GROUP), (METRICS_STREAM, METRICS_GROUP)):
            try:
                await redis.xgroup_create(stream, group, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc).upper():
                    raise
        self._groups_ready = True

    async def enqueue_scan_category(self, *, scan_id: int, catalog_id: int, category_name: str, pages: int) -> str:
        await self.ensure_groups()
        redis = await self.connect()
        return str(await redis.xadd(SCAN_STREAM, {
            "scan_id": str(int(scan_id)),
            "catalog_id": str(int(catalog_id)),
            "category_name": category_name[:240],
            "pages": str(int(pages)),
            "queued_at": str(int(time.time())),
        }))

    async def enqueue_metric(self, *, scan_id: int, item_id: int) -> str:
        await self.ensure_groups()
        redis = await self.connect()
        marker = f"{VINTED_REDIS_PREFIX}:metric_enqueued:{scan_id}:{item_id}"
        created = await redis.set(marker, "1", ex=24 * 3600, nx=True)
        if not created:
            return "duplicate"
        try:
            return str(await redis.xadd(METRICS_STREAM, {
                "scan_id": str(int(scan_id)),
                "item_id": str(int(item_id)),
                "queued_at": str(int(time.time())),
            }))
        except Exception:
            await redis.delete(marker)
            raise

    async def clear_metric_marker(self, scan_id: int, item_id: int) -> None:
        redis = await self.connect()
        await redis.delete(f"{VINTED_REDIS_PREFIX}:metric_enqueued:{scan_id}:{item_id}")

    async def enqueue_radar_followup(self, *, watch_id: int, scan_id: int, item_id: int, step: int) -> str:
        """Queue one identity-bound Radar follow-up on the existing Metrics fleet.

        The Redis marker is per watch/step, not per scan item, so +30/+60/+120/+180
        checkpoints can all run while duplicate scheduler ticks remain idempotent.
        """
        await self.ensure_groups()
        redis = await self.connect()
        marker = f"{VINTED_REDIS_PREFIX}:radar_followup_enqueued:{int(watch_id)}:{int(step)}"
        created = await redis.set(marker, "1", ex=30 * 60, nx=True)
        if not created:
            return "duplicate"
        try:
            return str(await redis.xadd(METRICS_STREAM, {
                "purpose": "radar_followup",
                "watch_id": str(int(watch_id)),
                "scan_id": str(int(scan_id)),
                "item_id": str(int(item_id)),
                "step": str(int(step)),
                "queued_at": str(int(time.time())),
            }))
        except Exception:
            await redis.delete(marker)
            raise

    async def clear_radar_followup_marker(self, watch_id: int, step: int) -> None:
        redis = await self.connect()
        await redis.delete(f"{VINTED_REDIS_PREFIX}:radar_followup_enqueued:{int(watch_id)}:{int(step)}")

    async def claim(self, *, role: str, worker_id: str, block_ms: int = 5000) -> tuple[str, dict[str, str]] | None:
        await self.ensure_groups()
        redis = await self.connect()
        stream, group = (SCAN_STREAM, SCAN_GROUP) if role == "scan" else (METRICS_STREAM, METRICS_GROUP)
        try:
            claimed = await redis.xautoclaim(stream, group, worker_id, min_idle_time=PENDING_RECLAIM_IDLE_MS, start_id="0-0", count=1)
            rows = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) >= 2 else []
            if rows:
                msg_id, fields = rows[0]
                return str(msg_id), {str(k): str(v) for k, v in dict(fields).items()}
        except Exception:
            pass
        rows = await redis.xreadgroup(group, worker_id, {stream: ">"}, count=1, block=block_ms)
        if not rows:
            return None
        _stream_name, messages = rows[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        return str(msg_id), {str(k): str(v) for k, v in dict(fields).items()}

    async def ack(self, *, role: str, msg_id: str) -> None:
        redis = await self.connect()
        stream, group = (SCAN_STREAM, SCAN_GROUP) if role == "scan" else (METRICS_STREAM, METRICS_GROUP)
        await redis.xack(stream, group, msg_id)

    async def heartbeat(self, *, role: str, worker_id: str, payload: dict[str, Any]) -> None:
        redis = await self.connect()
        key = f"{VINTED_REDIS_PREFIX}:heartbeat:{role}:{worker_id}"
        body = dict(payload)
        body.update({"role": role, "worker_id": worker_id, "version": APP_VERSION, "ts": time.time()})
        await redis.set(key, json.dumps(body, ensure_ascii=False, separators=(",", ":")), ex=HEARTBEAT_TTL_SECONDS)

    async def worker_status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "scan_workers": [], "metrics_workers": [], "scan_queue": 0, "metrics_queue": 0}
        try:
            await self.ensure_groups()
            redis = await self.connect()
            result: dict[str, Any] = {"enabled": True, "scan_workers": [], "metrics_workers": []}
            for role, target in (("scan", "scan_workers"), ("metrics", "metrics_workers")):
                pattern = f"{VINTED_REDIS_PREFIX}:heartbeat:{role}:*"
                async for key in redis.scan_iter(match=pattern):
                    raw = await redis.get(key)
                    try:
                        data = json.loads(raw) if raw else {}
                    except Exception:
                        data = {}
                    if isinstance(data, dict):
                        result[target].append(data)
            async def group_depth(stream: str, group: str) -> int:
                try:
                    groups = await redis.xinfo_groups(stream)
                    for row in groups:
                        if str(row.get("name") or "") == group:
                            pending = int(row.get("pending") or 0)
                            lag = int(row.get("lag") or 0) if row.get("lag") is not None else 0
                            return max(0, pending + lag)
                except Exception:
                    return 0
                return 0
            result["scan_queue"] = await group_depth(SCAN_STREAM, SCAN_GROUP)
            result["metrics_queue"] = await group_depth(METRICS_STREAM, METRICS_GROUP)
            return result
        except Exception as exc:
            return {"enabled": True, "error": str(exc), "scan_workers": [], "metrics_workers": [], "scan_queue": 0, "metrics_queue": 0}


VINTED_QUEUE = VintedLabQueue()


def make_worker_id(role: str) -> str:
    return f"{role}-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


async def create_scan(*, admin_user_id: int, mode: str, categories: list[tuple[int, str]], pages: int) -> VintedScan:
    mode = "radar" if mode == "radar" else "manual"
    pages = max(1, min(15, int(pages)))
    uid = uuid.uuid4().hex[:16]
    now = utcnow()
    async with SessionLocal() as session:
        row = VintedScan(
            uid=uid,
            admin_user_id=int(admin_user_id),
            mode=mode,
            status="queued",
            pages_per_category=pages,
            total_categories=len(categories),
            completed_categories=0,
            total_items=0,
            metrics_total=0,
            metrics_done=0,
            exact_views=0,
            exact_favourites=0,
            chronology_count=0,
            created_at=now,
            updated_at=now,
            stage="queued",
            selected_catalogs=json.dumps([{"id": int(cid), "name": name} for cid, name in categories], ensure_ascii=False),
        )
        session.add(row)
        await session.flush()
        for catalog_id, name in categories:
            session.add(VintedScanCategory(
                scan_id=row.id,
                catalog_id=int(catalog_id),
                category_name=str(name)[:255],
                status="queued",
                pages_target=pages,
                pages_fetched=0,
                unique_items=0,
                duplicate_count=0,
                created_at=now,
                updated_at=now,
            ))
        await session.commit()
        await session.refresh(row)
        return row


async def enqueue_scan(scan_id: int) -> int:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None:
            return 0
        rows = (await session.execute(
            select(VintedScanCategory).where(VintedScanCategory.scan_id == int(scan_id)).order_by(VintedScanCategory.id)
        )).scalars().all()
        count = 0
        for row in rows:
            await VINTED_QUEUE.enqueue_scan_category(
                scan_id=scan.id,
                catalog_id=row.catalog_id,
                category_name=row.category_name,
                pages=row.pages_target,
            )
            count += 1
        scan.status = "queued"
        scan.stage = "waiting_scan_worker"
        scan.updated_at = utcnow()
        await session.commit()
        return count


async def get_scan(scan_id: int) -> VintedScan | None:
    async with SessionLocal() as session:
        return await session.get(VintedScan, int(scan_id))


async def scan_collects_detail_metrics(scan_id: int) -> bool:
    """Manual parser may probe detail metrics; Radar 1.0 is catalog-only.

    Vinted Radar uses catalog likes and repeated catalog snapshots, so blocked detail
    endpoints must never keep a Radar round in the metrics stage.
    """
    async with SessionLocal() as session:
        row = await session.get(VintedScan, int(scan_id))
        return bool(row is not None and str(row.mode or "manual") != "radar")


async def get_scan_categories(scan_id: int) -> list[VintedScanCategory]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(VintedScanCategory).where(VintedScanCategory.scan_id == int(scan_id)).order_by(VintedScanCategory.id)
        )).scalars().all())


async def list_scans(limit: int = 12) -> list[VintedScan]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(VintedScan).order_by(VintedScan.created_at.desc()).limit(max(1, min(50, int(limit))))
        )).scalars().all())


async def list_scan_items(scan_id: int, *, offset: int = 0, limit: int = 10) -> tuple[list[VintedScanItem], int]:
    """Page saved items without re-sorting/counting a huge Radar table on every click."""
    scan_id = int(scan_id)
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, scan_id)
        if scan is None:
            return [], 0
        radar_mode = str(scan.mode or "manual") == "radar"
        if radar_mode:
            # Radar total_items is maintained atomically in save_catalog_page().  Avoid a
            # COUNT(*) and a useless view_count sort over a full-market catalog-only scan.
            total = int(scan.total_items or 0)
            order_by = (VintedScanItem.id.asc(),)
        else:
            total = int((await session.execute(
                select(func.count(VintedScanItem.id)).where(VintedScanItem.scan_id == scan_id)
            )).scalar_one() or 0)
            order_by = (VintedScanItem.view_count.is_(None), VintedScanItem.view_count.desc(), VintedScanItem.id.asc())
        rows = list((await session.execute(
            select(VintedScanItem)
            .where(VintedScanItem.scan_id == scan_id)
            .order_by(*order_by)
            .offset(max(0, int(offset)))
            .limit(max(1, min(25, int(limit))))
        )).scalars().all())
        return rows, total


async def get_scan_item(scan_id: int, item_id: int) -> VintedScanItem | None:
    async with SessionLocal() as session:
        return (await session.execute(
            select(VintedScanItem).where(
                VintedScanItem.scan_id == int(scan_id),
                VintedScanItem.item_id == int(item_id),
            )
        )).scalar_one_or_none()


async def cancel_scan(scan_id: int) -> bool:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None or scan.status in {"completed", "partial", "failed", "cancelled"}:
            return False
        now = utcnow()
        scan.status = "cancel_requested"
        scan.stage = "stopping"
        scan.updated_at = now
        # Queued Redis messages can still be ACKed after a stop, but they must not keep
        # the database scan permanently non-terminal. Running categories are allowed to
        # finish their current bounded task; every not-yet-started category is cancelled
        # immediately. This is also what makes the v4.23.3 leaf->segment migration safe.
        await session.execute(
            update(VintedScanCategory)
            .where(VintedScanCategory.scan_id == int(scan_id), VintedScanCategory.status == "queued")
            .values(status="cancelled", finished_at=now, updated_at=now)
        )
        await session.commit()
    await recalc_scan(scan_id)
    return True


async def recalc_scan(scan_id: int) -> VintedScan | None:
    """Refresh scan counters without loading the whole item table into Python.

    v4.23.4: Radar scans can contain tens of thousands of catalog rows.  The old
    implementation selected every VintedScanItem after *every* completed segment,
    which made a full-market pass progressively slower and could starve the bot DB
    pool.  Radar has no detail-metrics stage, so its item counters are already kept
    incrementally by save_catalog_page().  Manual scans use one SQL aggregate instead.
    """
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None:
            return None

        cats = list((await session.execute(
            select(VintedScanCategory).where(VintedScanCategory.scan_id == scan.id)
        )).scalars().all())
        scan.completed_categories = sum(1 for row in cats if row.status in {"completed", "failed", "cancelled", "partial"})

        radar_catalog_only = str(scan.mode or "manual") == "radar"
        if radar_catalog_only:
            # save_catalog_page() already increments total_items for each newly inserted
            # unique item.  Radar deliberately has no exact/detail metrics.
            scan.metrics_total = 0
            scan.metrics_done = 0
            scan.exact_views = 0
            scan.exact_favourites = 0
            scan.chronology_count = 0
        else:
            terminal_metric_states = ("exact", "unknown", "error", "cancelled")
            metric_row = (await session.execute(
                select(
                    func.count(VintedScanItem.id),
                    func.coalesce(func.sum(case((VintedScanItem.metric_status.in_(terminal_metric_states), 1), else_=0)), 0),
                    func.coalesce(func.sum(case((VintedScanItem.view_count.is_not(None), 1), else_=0)), 0),
                    func.coalesce(func.sum(case((VintedScanItem.favourite_count.is_not(None), 1), else_=0)), 0),
                    func.coalesce(func.sum(case((VintedScanItem.upload_raw.is_not(None), 1), else_=0)), 0),
                ).where(VintedScanItem.scan_id == scan.id)
            )).one()
            scan.total_items = int(metric_row[0] or 0)
            scan.metrics_total = scan.total_items
            scan.metrics_done = int(metric_row[1] or 0)
            scan.exact_views = int(metric_row[2] or 0)
            scan.exact_favourites = int(metric_row[3] or 0)
            scan.chronology_count = int(metric_row[4] or 0)

        if scan.status == "cancel_requested":
            if all(row.status in {"completed", "failed", "cancelled", "partial"} for row in cats):
                scan.status = "cancelled"
                scan.stage = "cancelled"
                scan.finished_at = utcnow()
        else:
            categories_terminal = bool(cats) and all(row.status in {"completed", "failed", "cancelled", "partial"} for row in cats)
            if categories_terminal:
                if int(scan.metrics_done or 0) >= int(scan.metrics_total or 0):
                    any_failed = any(row.status in {"failed", "partial"} for row in cats)
                    scan.status = "partial" if any_failed else "completed"
                    scan.stage = "done"
                    scan.finished_at = scan.finished_at or utcnow()
                else:
                    scan.status = "metrics"
                    scan.stage = "collecting_metrics"
            elif any(row.status == "running" for row in cats):
                scan.status = "running"
                scan.stage = "scanning_catalogs"
        scan.updated_at = utcnow()
        await session.commit()
        await session.refresh(scan)
        return scan


async def mark_category_running(scan_id: int, catalog_id: int) -> bool:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None or scan.status in {"cancel_requested", "cancelled"}:
            return False
        row = (await session.execute(select(VintedScanCategory).where(
            VintedScanCategory.scan_id == int(scan_id), VintedScanCategory.catalog_id == int(catalog_id)
        ))).scalar_one_or_none()
        if row is None:
            return False
        row.status = "running"
        row.started_at = row.started_at or utcnow()
        row.updated_at = utcnow()
        scan.status = "running"
        scan.stage = "scanning_catalogs"
        scan.started_at = scan.started_at or utcnow()
        scan.updated_at = utcnow()
        await session.commit()
        return True


async def save_catalog_page(
    *, scan_id: int, catalog_id: int, category_name: str, page: int,
    items: Iterable[Any], duplicate_count: int = 0,
) -> list[int]:
    """Persist one catalog page and return newly inserted item IDs for metrics queue."""
    raw_items = list(items)
    ids = [int(getattr(item, "item_id", 0) or 0) for item in raw_items if int(getattr(item, "item_id", 0) or 0) > 0]
    new_ids: list[int] = []
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None or scan.status in {"cancel_requested", "cancelled"}:
            return []
        existing: set[int] = set()
        if ids:
            existing = set((await session.execute(
                select(VintedScanItem.item_id).where(
                    VintedScanItem.scan_id == int(scan_id), VintedScanItem.item_id.in_(ids)
                )
            )).scalars().all())
        now = utcnow()
        for item in raw_items:
            item_id = int(getattr(item, "item_id", 0) or 0)
            if item_id <= 0 or item_id in existing:
                continue
            item_catalog_id = _int(getattr(item, "catalog_id", None), 0) or int(catalog_id)
            row = VintedScanItem(
                scan_id=int(scan_id), item_id=item_id, catalog_id=item_catalog_id, category_name=category_name[:255],
                title=str(getattr(item, "title", "") or "")[:500], url=str(getattr(item, "url", "") or "")[:1200],
                price_amount=getattr(item, "price_amount", None), currency=str(getattr(item, "currency", "") or "")[:16],
                brand=str(getattr(item, "brand", "") or "")[:255], size=str(getattr(item, "size", "") or "")[:120],
                condition=str(getattr(item, "condition", "") or "")[:120], seller_id=getattr(item, "seller_id", None),
                seller_login=str(getattr(item, "seller_login", "") or "")[:120], promoted=getattr(item, "promoted", None),
                visible=getattr(item, "visible", None), catalog_view_count=getattr(item, "catalog_view_count", None),
                catalog_favourite_count=getattr(item, "catalog_favourite_count", None), metric_status="queued",
                created_at=now, updated_at=now,
            )
            session.add(row)
            existing.add(item_id)
            new_ids.append(item_id)
        cat = (await session.execute(select(VintedScanCategory).where(
            VintedScanCategory.scan_id == int(scan_id), VintedScanCategory.catalog_id == int(catalog_id)
        ))).scalar_one_or_none()
        if cat is not None:
            cat.pages_fetched = max(int(cat.pages_fetched or 0), int(page))
            cat.duplicate_count = int(cat.duplicate_count or 0) + max(0, int(duplicate_count))
            cat.updated_at = now
        scan.updated_at = now
        # Flush the ordinary row changes first, then increment counters atomically.
        # Multiple Vinted Scan Workers can finish pages at the same time; a Python
        # read-modify-write on scan.total_items can lose increments under concurrency.
        await session.flush()
        if new_ids:
            await session.execute(
                update(VintedScan)
                .where(VintedScan.id == int(scan_id))
                .values(
                    total_items=func.coalesce(VintedScan.total_items, 0) + len(new_ids),
                    metrics_total=func.coalesce(VintedScan.metrics_total, 0) + len(new_ids),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
        await session.commit()
    return new_ids


async def complete_category(
    *, scan_id: int, catalog_id: int, status: str, pages_fetched: int,
    unique_items: int, duplicate_count: int, error_text: str = "",
) -> None:
    async with SessionLocal() as session:
        row = (await session.execute(select(VintedScanCategory).where(
            VintedScanCategory.scan_id == int(scan_id), VintedScanCategory.catalog_id == int(catalog_id)
        ))).scalar_one_or_none()
        if row is not None:
            row.status = status if status in {"completed", "partial", "failed", "cancelled"} else "failed"
            row.pages_fetched = max(int(row.pages_fetched or 0), int(pages_fetched))
            row.unique_items = max(int(row.unique_items or 0), int(unique_items))
            row.duplicate_count = max(int(row.duplicate_count or 0), int(duplicate_count))
            row.error_text = str(error_text or "")[:1000]
            row.finished_at = utcnow()
            row.updated_at = utcnow()
        await session.commit()
    await recalc_scan(scan_id)


async def load_metric_item(scan_id: int, item_id: int) -> VintedScanItem | None:
    async with SessionLocal() as session:
        return (await session.execute(select(VintedScanItem).where(
            VintedScanItem.scan_id == int(scan_id), VintedScanItem.item_id == int(item_id)
        ))).scalar_one_or_none()


async def mark_metric_processing(scan_id: int, item_id: int) -> bool:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None or scan.status in {"cancel_requested", "cancelled"}:
            return False
        row = (await session.execute(select(VintedScanItem).where(
            VintedScanItem.scan_id == int(scan_id), VintedScanItem.item_id == int(item_id)
        ))).scalar_one_or_none()
        if row is None:
            return False
        if row.metric_status in {"exact", "unknown", "error", "cancelled"}:
            return False
        row.metric_status = "processing"
        row.updated_at = utcnow()
        await session.commit()
        return True


async def save_metric_sample(scan_id: int, item_id: int, sample: Any) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        row = (await session.execute(select(VintedScanItem).where(
            VintedScanItem.scan_id == int(scan_id), VintedScanItem.item_id == int(item_id)
        ))).scalar_one_or_none()
        if row is None:
            return
        row.view_count = getattr(sample, "view_count", None)
        row.favourite_count = getattr(sample, "favourite_count", None)
        upload_raw = getattr(sample, "upload_raw", None)
        row.upload_raw = None if upload_raw is None else str(upload_raw)[:255]
        row.metric_source = str(getattr(sample, "source", "") or "")[:80]
        row.metric_outcome = str(getattr(sample, "outcome", "unknown") or "unknown")[:80]
        row.identity_ok = bool(getattr(sample, "identity_ok", False))
        row.sold = getattr(sample, "sold", None)
        row.closed = getattr(sample, "closed", None)
        row.reserved = getattr(sample, "reserved", None)
        row.hidden = getattr(sample, "hidden", None)
        row.metric_status = "exact" if row.identity_ok and row.view_count is not None else "unknown"
        row.metrics_checked_at = now
        row.updated_at = now
        session.add(VintedMetricHistory(
            scan_id=int(scan_id), item_id=int(item_id), measured_at=now,
            view_count=row.view_count, favourite_count=row.favourite_count, upload_raw=row.upload_raw,
            source=row.metric_source, outcome=row.metric_outcome, identity_ok=row.identity_ok,
        ))
        await session.commit()
    await recalc_scan(scan_id)


async def mark_metric_error(scan_id: int, item_id: int, error_text: str) -> None:
    async with SessionLocal() as session:
        row = (await session.execute(select(VintedScanItem).where(
            VintedScanItem.scan_id == int(scan_id), VintedScanItem.item_id == int(item_id)
        ))).scalar_one_or_none()
        if row is not None:
            row.metric_status = "error"
            row.metric_outcome = str(error_text or "error")[:80]
            row.metrics_checked_at = utcnow()
            row.updated_at = utcnow()
        await session.commit()
    await recalc_scan(scan_id)


async def load_radar_followup_item(watch_id: int) -> tuple[VintedRadarWatch, VintedScanItem] | None:
    """Load a durable Radar watch and the catalog row that supplied its baseline."""
    async with SessionLocal() as session:
        watch = await session.get(VintedRadarWatch, int(watch_id))
        if watch is None:
            return None
        item = (await session.execute(select(VintedScanItem).where(
            VintedScanItem.scan_id == int(watch.source_scan_id),
            VintedScanItem.item_id == int(watch.item_id),
        ))).scalar_one_or_none()
        if item is None:
            return None
        return watch, item


async def claim_radar_followup_processing(watch_id: int, step: int, *, lease_seconds: int = 300) -> bool:
    """Claim exactly one queued follow-up message; stale/duplicate Redis messages no-op."""
    now = utcnow()
    async with SessionLocal() as session:
        watch = await session.get(VintedRadarWatch, int(watch_id))
        if watch is None or int(watch.check_step or 0) != int(step):
            return False
        if str(watch.status or "") not in {"queued", "watching", "processing"}:
            return False
        if str(watch.status or "") == "processing" and watch.lease_until and watch.lease_until > now:
            return False
        watch.status = "processing"
        watch.lease_until = now + timedelta(seconds=max(60, int(lease_seconds)))
        watch.updated_at = now
        await session.commit()
        return True


def _next_followup_due(first_seen: datetime, next_step: int, now: datetime) -> datetime | None:
    if next_step >= len(VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES):
        return None
    target = first_seen + timedelta(minutes=int(VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES[next_step]))
    # Never create two measurements almost back-to-back when a worker was delayed.
    return max(target, now + timedelta(minutes=5))


async def save_radar_followup_sample(watch_id: int, step: int, sample: Any) -> str:
    """Persist one targeted favourite-count sample and advance the durable schedule."""
    now = utcnow()
    async with SessionLocal() as session:
        watch = await session.get(VintedRadarWatch, int(watch_id))
        if watch is None or int(watch.check_step or 0) != int(step):
            return "stale"
        item = (await session.execute(select(VintedScanItem).where(
            VintedScanItem.scan_id == int(watch.source_scan_id),
            VintedScanItem.item_id == int(watch.item_id),
        ))).scalar_one_or_none()
        if item is None:
            watch.status = "failed"
            watch.last_outcome = "source_item_missing"
            watch.next_check_at = None
            watch.lease_until = None
            watch.updated_at = now
            await session.commit()
            return "source_item_missing"

        identity_ok = bool(getattr(sample, "identity_ok", False))
        favourite_count = getattr(sample, "favourite_count", None)
        outcome = str(getattr(sample, "outcome", "unknown") or "unknown")[:80]
        source = str(getattr(sample, "source", "") or "")[:56]
        exact_like = identity_ok and favourite_count is not None

        watch.checks = int(watch.checks or 0) + 1
        watch.last_checked_at = now
        watch.last_outcome = outcome
        watch.lease_until = None
        watch.updated_at = now

        if exact_like:
            fav = int(favourite_count)
            session.add(VintedMetricHistory(
                scan_id=int(watch.source_scan_id),
                item_id=int(watch.item_id),
                measured_at=now,
                view_count=None,
                favourite_count=fav,
                upload_raw=None,
                source=(f"radar_followup:{source}" if source else "radar_followup")[:80],
                outcome=outcome,
                identity_ok=True,
            ))
            watch.last_likes = fav
            watch.exact_samples = int(watch.exact_samples or 0) + 1
            watch.retry_count = 0
            watch.check_step = int(watch.check_step or 0) + 1
            next_due = _next_followup_due(watch.first_seen_at, int(watch.check_step), now)
            watch.next_check_at = next_due
            watch.status = "completed" if next_due is None else "watching"
            if bool(getattr(sample, "sold", False) or getattr(sample, "closed", False) or getattr(sample, "hidden", False)):
                watch.status = "completed"
                watch.next_check_at = None
                watch.last_outcome = "closed_or_hidden"
            await session.commit()
            return "exact"

        # Protected/UNKNOWN responses remain fail-closed. Retry a step once by default;
        # after that, advance so one bad endpoint response cannot stall a watch forever.
        retries = int(watch.retry_count or 0) + 1
        if retries <= VINTED_RADAR_FOLLOWUP_RETRIES_PER_STEP:
            watch.retry_count = retries
            watch.status = "watching"
            watch.next_check_at = now + timedelta(minutes=VINTED_RADAR_FOLLOWUP_RETRY_MINUTES)
        else:
            watch.retry_count = 0
            watch.check_step = int(watch.check_step or 0) + 1
            next_due = _next_followup_due(watch.first_seen_at, int(watch.check_step), now)
            watch.next_check_at = next_due
            watch.status = "completed" if next_due is None else "watching"
        await session.commit()
        return "unknown"


async def mark_radar_followup_error(watch_id: int, step: int, error_text: str) -> None:
    """Release a failed processing lease and retry later without inventing evidence."""
    now = utcnow()
    async with SessionLocal() as session:
        watch = await session.get(VintedRadarWatch, int(watch_id))
        if watch is None or int(watch.check_step or 0) != int(step):
            return
        reason = str(error_text or "worker_error")[:80]
        watch.last_outcome = reason
        watch.lease_until = None
        if reason == "source_item_missing":
            watch.status = "failed"
            watch.next_check_at = None
        else:
            retries = int(watch.retry_count or 0) + 1
            if retries <= VINTED_RADAR_FOLLOWUP_RETRIES_PER_STEP:
                watch.retry_count = retries
                watch.status = "watching"
                watch.next_check_at = now + timedelta(minutes=VINTED_RADAR_FOLLOWUP_RETRY_MINUTES)
            else:
                watch.retry_count = 0
                watch.check_step = int(watch.check_step or 0) + 1
                next_due = _next_followup_due(watch.first_seen_at, int(watch.check_step), now)
                watch.next_check_at = next_due
                watch.status = "completed" if next_due is None else "watching"
        watch.updated_at = now
        await session.commit()


async def scan_progress_snapshot(scan_id: int) -> dict[str, Any] | None:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None:
            return None
        cats = list((await session.execute(
            select(VintedScanCategory).where(VintedScanCategory.scan_id == scan.id).order_by(VintedScanCategory.id)
        )).scalars().all())
        if str(scan.mode or "manual") == "radar":
            # v4.23.4: the live page-progress screen is polled frequently.  Running a
            # SUM/MAX/COUNT over a full-market Radar item table on every poll can lock
            # up the shared database pool for everyone.  Radar scoring consumes likes
            # from the background snapshot; page progress only needs persisted counters.
            catalog_likes = {
                "items": int(scan.total_items or 0),
                "known": None,
                "nonzero": None,
                "total": None,
                "max": None,
                "deferred": True,
            }
        else:
            like_row = (await session.execute(
                select(
                    func.count(VintedScanItem.id),
                    func.count(VintedScanItem.catalog_favourite_count),
                    func.coalesce(func.sum(case((VintedScanItem.catalog_favourite_count > 0, 1), else_=0)), 0),
                    func.coalesce(func.sum(VintedScanItem.catalog_favourite_count), 0),
                    func.coalesce(func.max(VintedScanItem.catalog_favourite_count), 0),
                ).where(VintedScanItem.scan_id == scan.id)
            )).one()
            catalog_likes = {
                "items": int(like_row[0] or 0),
                "known": int(like_row[1] or 0),
                "nonzero": int(like_row[2] or 0),
                "total": int(like_row[3] or 0),
                "max": int(like_row[4] or 0),
                "deferred": False,
            }
        scan_units = 0.0
        page_plan_max = 0
        page_primary_done = 0
        page_fetched_total = 0
        status_counts = {"queued": 0, "running": 0, "completed": 0, "partial": 0, "failed": 0, "cancelled": 0}
        for row in cats:
            target = max(1, int(row.pages_target or scan.pages_per_category or 1))
            fetched = max(0, int(row.pages_fetched or 0))
            page_plan_max += target
            page_primary_done += min(target, fetched)
            page_fetched_total += fetched
            status_counts[str(row.status or "queued")] = status_counts.get(str(row.status or "queued"), 0) + 1
            if row.status in {"completed", "partial", "failed", "cancelled"}:
                scan_units += 1.0
            else:
                scan_units += min(0.99, max(0.0, fetched / target))
        scan_percent = round(100.0 * scan_units / max(1, len(cats)), 1)
        page_percent = round(100.0 * page_primary_done / max(1, page_plan_max), 1) if page_plan_max else 0.0
        metrics_percent = round(100.0 * int(scan.metrics_done or 0) / max(1, int(scan.metrics_total or 0)), 1) if int(scan.metrics_total or 0) else 0.0
        return {
            "scan": scan,
            "categories": cats,
            "scan_percent": scan_percent,
            "page_percent": page_percent,
            "metrics_percent": metrics_percent,
            "catalog_likes": catalog_likes,
            "pages": {
                "plan_max": page_plan_max,
                "primary_done": page_primary_done,
                "fetched_total": page_fetched_total,
                "recovery": max(0, page_fetched_total - page_primary_done),
            },
            "category_status": status_counts,
        }


async def catalog_like_delta(scan_id: int, item_id: int) -> dict[str, int | None]:
    """Compare public catalog favourite_count with the previous saved scan of the same item.

    This never guesses missing values: absent catalog counts stay UNKNOWN.
    """
    async with SessionLocal() as session:
        current_scan = await session.get(VintedScan, int(scan_id))
        if current_scan is None:
            return {"current": None, "previous": None, "delta": None, "previous_scan_id": None}
        current = (await session.execute(
            select(VintedScanItem.catalog_favourite_count).where(
                VintedScanItem.scan_id == int(scan_id),
                VintedScanItem.item_id == int(item_id),
            )
        )).scalar_one_or_none()
        if current is None:
            return {"current": None, "previous": None, "delta": None, "previous_scan_id": None}
        prev = (await session.execute(
            select(VintedScanItem.scan_id, VintedScanItem.catalog_favourite_count)
            .join(VintedScan, VintedScan.id == VintedScanItem.scan_id)
            .where(
                VintedScanItem.item_id == int(item_id),
                VintedScanItem.scan_id != int(scan_id),
                VintedScanItem.catalog_favourite_count.is_not(None),
                VintedScan.created_at < current_scan.created_at,
            )
            .order_by(VintedScan.created_at.desc(), VintedScanItem.id.desc())
            .limit(1)
        )).first()
        if prev is None:
            return {"current": int(current), "previous": None, "delta": None, "previous_scan_id": None}
        previous = int(prev[1])
        return {
            "current": int(current),
            "previous": previous,
            "delta": int(current) - previous,
            "previous_scan_id": int(prev[0]),
        }
