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
from datetime import datetime
from typing import Any, Iterable

import httpx
from sqlalchemy import func, select, update

from app_version import APP_VERSION
from db import SessionLocal
from models import VintedMetricHistory, VintedScan, VintedScanCategory, VintedScanItem
from vinted_probe import DEFAULT_BASE_URL, DEFAULT_USER_AGENT

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


async def fetch_catalog_tree(*, force: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Fetch current Vinted DE category roots, with a safe local fallback.

    Category metadata is configuration, not demand evidence. Failure here must never
    block already-configured scans; the admin can still test the broad fallback roots.
    """
    global _catalog_cache
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
                raw_session = os.getenv("VINTED_SESSION_JSON", "").strip()
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
    pages = max(1, min(10, int(pages)))
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
    async with SessionLocal() as session:
        total = int((await session.execute(
            select(func.count(VintedScanItem.id)).where(VintedScanItem.scan_id == int(scan_id))
        )).scalar_one() or 0)
        rows = list((await session.execute(
            select(VintedScanItem)
            .where(VintedScanItem.scan_id == int(scan_id))
            .order_by(VintedScanItem.view_count.is_(None), VintedScanItem.view_count.desc(), VintedScanItem.id.asc())
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
        scan.status = "cancel_requested"
        scan.stage = "stopping"
        scan.updated_at = utcnow()
        await session.commit()
        return True


async def recalc_scan(scan_id: int) -> VintedScan | None:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None:
            return None
        cats = list((await session.execute(
            select(VintedScanCategory).where(VintedScanCategory.scan_id == scan.id)
        )).scalars().all())
        scan.completed_categories = sum(1 for row in cats if row.status in {"completed", "failed", "cancelled", "partial"})
        metric_rows = list((await session.execute(
            select(
                VintedScanItem.metric_status,
                VintedScanItem.view_count,
                VintedScanItem.favourite_count,
                VintedScanItem.upload_raw,
            ).where(VintedScanItem.scan_id == scan.id)
        )).all())
        scan.total_items = len(metric_rows)
        scan.metrics_total = scan.total_items
        terminal_metric_states = {"exact", "unknown", "error", "cancelled"}
        scan.metrics_done = sum(1 for status, _views, _fav, _upload in metric_rows if str(status or "") in terminal_metric_states)
        scan.exact_views = sum(1 for _status, views, _fav, _upload in metric_rows if views is not None)
        scan.exact_favourites = sum(1 for _status, _views, fav, _upload in metric_rows if fav is not None)
        scan.chronology_count = sum(1 for _status, _views, _fav, upload in metric_rows if upload is not None)
        if scan.status == "cancel_requested":
            if all(row.status in {"completed", "failed", "cancelled", "partial"} for row in cats):
                scan.status = "cancelled"
                scan.stage = "cancelled"
                scan.finished_at = utcnow()
        else:
            categories_terminal = bool(cats) and all(row.status in {"completed", "failed", "cancelled", "partial"} for row in cats)
            if categories_terminal:
                if scan.metrics_done >= scan.metrics_total:
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
            row = VintedScanItem(
                scan_id=int(scan_id), item_id=item_id, catalog_id=int(catalog_id), category_name=category_name[:255],
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
        if new_ids:
            scan.total_items = int(scan.total_items or 0) + len(new_ids)
            scan.metrics_total = int(scan.metrics_total or 0) + len(new_ids)
        scan.updated_at = now
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


async def scan_progress_snapshot(scan_id: int) -> dict[str, Any] | None:
    async with SessionLocal() as session:
        scan = await session.get(VintedScan, int(scan_id))
        if scan is None:
            return None
        cats = list((await session.execute(
            select(VintedScanCategory).where(VintedScanCategory.scan_id == scan.id).order_by(VintedScanCategory.id)
        )).scalars().all())
        scan_units = 0.0
        for row in cats:
            if row.status in {"completed", "partial", "failed", "cancelled"}:
                scan_units += 1.0
            else:
                target = max(1, int(row.pages_target or scan.pages_per_category or 1))
                scan_units += min(0.99, max(0.0, int(row.pages_fetched or 0) / target))
        scan_percent = round(100.0 * scan_units / max(1, len(cats)), 1)
        metrics_percent = round(100.0 * int(scan.metrics_done or 0) / max(1, int(scan.metrics_total or 0)), 1) if int(scan.metrics_total or 0) else 0.0
        return {
            "scan": scan,
            "categories": cats,
            "scan_percent": scan_percent,
            "metrics_percent": metrics_percent,
        }
