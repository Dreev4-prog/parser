from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, func, select

from db import SessionLocal
from models import AppSetting, VintedMetricHistory, VintedRadarWatch, VintedScan, VintedScanCategory, VintedScanItem
from vinted_lab import (
    VINTED_QUEUE, VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES, balanced_catalog_segments_from_tree,
    cancel_scan, create_scan, enqueue_scan, fetch_catalog_tree, leaf_catalogs_from_tree, recalc_scan,
)

log = logging.getLogger("vinted-radar")

VINTED_RADAR_SETTING_KEY = "vinted_radar_1_0"
VINTED_RADAR_LIVE_HOURS = max(6, min(48, int(os.getenv("VINTED_RADAR_LIVE_HOURS", "24") or 24)))
VINTED_RADAR_HISTORY_DAYS = max(2, min(30, int(os.getenv("VINTED_RADAR_HISTORY_DAYS", "7") or 7)))
VINTED_RADAR_INTERVAL_MINUTES = max(15, min(360, int(os.getenv("VINTED_RADAR_INTERVAL_MINUTES", "60") or 60)))
VINTED_RADAR_CACHE_SECONDS = max(30, min(300, int(os.getenv("VINTED_RADAR_CACHE_SECONDS", "120") or 120)))
VINTED_RADAR_MIN_PRICE_EUR = max(0.0, min(5000.0, float(os.getenv("VINTED_RADAR_MIN_PRICE_EUR", "40") or 40)))
VINTED_RADAR_MIN_PRICE_PEERS = max(4, min(30, int(os.getenv("VINTED_RADAR_MIN_PRICE_PEERS", "8") or 8)))
VINTED_RADAR_PAGES_PER_CATEGORY = 15
# v4.23.3: the DE catalog contains thousands of terminal ids. Radar keeps complete
# market coverage by partitioning the tree into a bounded set of non-overlapping
# parent segments instead of scheduling every leaf as its own 15-page job.
VINTED_RADAR_SCOPE = "balanced_market_segments_v1"
VINTED_RADAR_MIN_LEAF_CATEGORIES = 20
VINTED_RADAR_TARGET_SEGMENTS = 120
VINTED_RADAR_MAX_SEGMENTS = 150

# v4.23.10: discovery and follow-up are separate lanes. The full catalog keeps its
# one-hour market cadence; a bounded promising subset receives targeted +30/+60/
# +120/+180 minute favourite-count checkpoints on the existing Metrics Worker fleet.
VINTED_RADAR_FOLLOWUP_ENABLED = str(os.getenv("VINTED_RADAR_FOLLOWUP_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}
VINTED_RADAR_FOLLOWUP_DISCOVERY_LOOKBACK_MINUTES = max(30, min(180, int(os.getenv("VINTED_RADAR_FOLLOWUP_DISCOVERY_LOOKBACK_MINUTES", "90") or 90)))
VINTED_RADAR_FOLLOWUP_MIN_DISCOVERY_SCORE = max(20, min(90, int(os.getenv("VINTED_RADAR_FOLLOWUP_MIN_DISCOVERY_SCORE", "42") or 42)))
VINTED_RADAR_FOLLOWUP_MAX_NEW_PER_HOUR = max(100, min(5000, int(os.getenv("VINTED_RADAR_FOLLOWUP_MAX_NEW_PER_HOUR", "1500") or 1500)))
VINTED_RADAR_FOLLOWUP_MAX_ACTIVE = max(200, min(10000, int(os.getenv("VINTED_RADAR_FOLLOWUP_MAX_ACTIVE", "4500") or 4500)))
VINTED_RADAR_FOLLOWUP_SWEEP_LIMIT = max(25, min(1000, int(os.getenv("VINTED_RADAR_FOLLOWUP_SWEEP_LIMIT", "300") or 300)))
VINTED_RADAR_FOLLOWUP_DISPATCH_BATCH = max(10, min(500, int(os.getenv("VINTED_RADAR_FOLLOWUP_DISPATCH_BATCH", "100") or 100)))
VINTED_RADAR_FOLLOWUP_SEED_INTERVAL_SECONDS = max(60, min(900, int(os.getenv("VINTED_RADAR_FOLLOWUP_SEED_INTERVAL_SECONDS", "120") or 120)))
VINTED_RADAR_FOLLOWUP_LEASE_SECONDS = max(120, min(1800, int(os.getenv("VINTED_RADAR_FOLLOWUP_LEASE_SECONDS", "360") or 360)))

_TERMINAL_SCAN_STATES = {"completed", "partial", "failed", "cancelled"}
_ACTIVE_SCAN_STATES = {"queued", "running", "metrics", "cancel_requested"}


def utcnow() -> datetime:
    return datetime.utcnow()


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _age_bucket(hours: float) -> str:
    if hours < 1.0:
        return "0-1h"
    if hours < 3.0:
        return "1-3h"
    if hours < 6.0:
        return "3-6h"
    if hours < 12.0:
        return "6-12h"
    return "12-24h"


def _percentile(value: float | int | None, peers: Iterable[float | int]) -> float:
    if value is None or float(value) <= 0:
        return 0.0
    clean = [float(x) for x in peers if x is not None and math.isfinite(float(x))]
    if not clean:
        return 0.0
    below = sum(1 for x in clean if x <= float(value))
    return max(0.0, min(1.0, below / len(clean)))


def _velocity_points(rate: float | None, percentile: float) -> int:
    if rate is None or rate <= 0:
        return 0
    if rate >= 4.0:
        absolute = 35
    elif rate >= 2.0:
        absolute = 30
    elif rate >= 1.0:
        absolute = 24
    elif rate >= 0.5:
        absolute = 18
    elif rate >= 0.25:
        absolute = 12
    else:
        absolute = 7
    relative = 0
    if percentile >= 0.97:
        relative = 35
    elif percentile >= 0.90:
        relative = 30
    elif percentile >= 0.75:
        relative = 24
    elif percentile >= 0.50:
        relative = 16
    elif percentile > 0:
        relative = 8
    return min(35, max(absolute, relative))


def _acceleration_points(acceleration: float | None) -> int:
    if acceleration is None or acceleration <= 0:
        return 0
    if acceleration >= 2.0:
        return 15
    if acceleration >= 1.0:
        return 12
    if acceleration >= 0.5:
        return 9
    if acceleration >= 0.2:
        return 6
    return 3


def _price_edge_points(edge_pct: float | None) -> int:
    if edge_pct is None or edge_pct >= 0:
        return 0
    if edge_pct <= -40:
        return 20
    if edge_pct <= -30:
        return 17
    if edge_pct <= -20:
        return 14
    if edge_pct <= -10:
        return 9
    return 4


def _peer_like_points(likes: int | None, percentile: float) -> int:
    if likes is None or likes <= 0:
        return 0
    if percentile >= 0.97:
        return 10
    if percentile >= 0.90:
        return 9
    if percentile >= 0.75:
        return 7
    if percentile >= 0.50:
        return 4
    return 2


def _scarcity_points(count: int, brand: str) -> int:
    if not brand:
        return 0
    if count <= 3:
        return 10
    if count <= 6:
        return 8
    if count <= 12:
        return 5
    if count <= 25:
        return 2
    return 0


def _seller_points(count: int, seller_id: int | None) -> int:
    if not seller_id:
        return 2
    if count <= 2:
        return 5
    if count <= 4:
        return 4
    if count <= 8:
        return 2
    return 0


def _brand_momentum_points(rate: float | None) -> int:
    if rate is None or rate <= 0:
        return 0
    if rate >= 2.0:
        return 5
    if rate >= 1.0:
        return 4
    if rate >= 0.5:
        return 3
    if rate >= 0.2:
        return 2
    return 1


@dataclass(slots=True)
class _RadarItem:
    id: int
    scan_id: int
    item_id: int
    catalog_id: int | None
    catalog_favourite_count: int | None
    price_amount: Any
    brand: str
    seller_id: int | None
    title: str
    url: str
    category_name: str
    currency: str
    size: str
    condition: str
    seller_login: str


@dataclass(slots=True)
class _Sample:
    scan_id: int
    scan_created_at: datetime
    item: _RadarItem


@dataclass(slots=True)
class VintedRadarEntry:
    item_id: int
    scan_id: int
    title: str
    url: str
    catalog_id: int
    category_name: str
    price_amount: float | None
    currency: str
    brand: str
    size: str
    condition: str
    seller_id: int | None
    seller_login: str
    first_seen_at: datetime
    last_seen_at: datetime
    age_hours: float
    age_bucket: str
    likes: int | None
    previous_likes: int | None
    like_delta: int | None
    sample_hours: float | None
    like_velocity: float | None
    acceleration: float | None
    price_median: float | None
    price_edge_pct: float | None
    price_peer_count: int
    like_percentile: float
    scarcity_count: int
    seller_active_count: int
    brand_velocity: float | None
    sample_count: int
    score: int
    status: str
    components: dict[str, int] = field(default_factory=dict)

    @property
    def has_confirmed_movement(self) -> bool:
        return self.sample_count >= 2 and self.like_delta is not None and self.like_delta > 0


@dataclass(slots=True)
class VintedRadarSnapshot:
    generated_at: datetime
    entries: list[VintedRadarEntry]
    live_total: int
    hot: int
    rising: int
    deals: int
    candidates: int
    baselines: int
    single_observation: int
    repeat_observation: int
    positive_movement: int
    followup_samples: int
    followup_active: int
    followup_due: int
    followup_completed: int
    history_items: int
    min_price_eur: float
    auto_enabled: bool
    auto_interval_minutes: int
    last_scan_id: int | None
    last_scan_at: datetime | None
    next_scan_at: datetime | None
    categories: list[dict[str, Any]]
    pages: int


VINTED_RADAR_UI_CONFIG_CACHE_SECONDS = 3.0
_config_cache: tuple[float, dict[str, Any]] | None = None


async def _load_setting() -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, VINTED_RADAR_SETTING_KEY)
        if row is None or not str(row.value or "").strip():
            return {}
        try:
            payload = json.loads(row.value)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}


async def _save_setting(payload: dict[str, Any]) -> None:
    global _config_cache
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    async with SessionLocal() as session:
        row = await session.get(AppSetting, VINTED_RADAR_SETTING_KEY)
        if row is None:
            row = AppSetting(key=VINTED_RADAR_SETTING_KEY, value=body, updated_at=utcnow())
            session.add(row)
        else:
            row.value = body
            row.updated_at = utcnow()
        await session.commit()
    _config_cache = None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


async def radar_config() -> dict[str, Any]:
    global _config_cache
    now_mono = time.monotonic()
    cached = _config_cache
    if cached is not None and now_mono - cached[0] <= VINTED_RADAR_UI_CONFIG_CACHE_SECONDS:
        return cached[1]
    raw = await _load_setting()
    categories = []
    for item in list(raw.get("categories") or []):
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("id") or 0)
        except Exception:
            cid = 0
        if cid > 0:
            categories.append({"id": cid, "name": str(item.get("name") or f"Catalog {cid}")[:255]})
    cfg = {
        "enabled": bool(raw.get("enabled", False)),
        "admin_user_id": int(raw.get("admin_user_id") or 0),
        "scope": str(raw.get("scope") or "legacy_selected"),
        "catalog_source": str(raw.get("catalog_source") or ""),
        "categories": categories,
        "pages": VINTED_RADAR_PAGES_PER_CATEGORY,
        "interval_minutes": max(15, min(360, int(raw.get("interval_minutes") or VINTED_RADAR_INTERVAL_MINUTES))),
        "last_scan_id": int(raw.get("last_scan_id") or 0) or None,
        "last_scan_at": _parse_dt(raw.get("last_scan_at")),
        "rounds_started": max(0, int(raw.get("rounds_started") or 0)),
        "updated_at": _parse_dt(raw.get("updated_at")),
    }
    _config_cache = (time.monotonic(), cfg)
    return cfg


async def resolve_all_market_categories(*, force: bool = False, cached: list[dict[str, Any]] | None = None, cached_scope: str = "") -> tuple[list[tuple[int, str]], str]:
    """Resolve the complete Vinted market into a bounded non-overlapping scan plan.

    The full German tree can contain thousands of terminal catalog ids. Scheduling
    15 pages for every leaf is wasteful and makes a one-hour Radar cadence impossible.
    We still validate the complete leaf tree, then partition it into roughly 120
    mixed-depth parent segments. Replacing a parent by its children preserves complete
    market coverage without parent/child overlap.
    """
    roots, source = await fetch_catalog_tree(force=force)
    if str(source).startswith(("live", "snapshot")):
        leaves = leaf_catalogs_from_tree(roots)
        if len(leaves) >= VINTED_RADAR_MIN_LEAF_CATEGORIES:
            segments = balanced_catalog_segments_from_tree(
                roots, target_segments=VINTED_RADAR_TARGET_SEGMENTS, max_segments=VINTED_RADAR_MAX_SEGMENTS,
            )
            if segments:
                plan_source = f"{source}|{len(segments)}seg/{len(leaves)}leaf"
                log.info(
                    "Vinted Radar market plan source=%s leaves=%s segments=%s pages_max=%s",
                    source, len(leaves), len(segments), len(segments) * VINTED_RADAR_PAGES_PER_CATEGORY,
                )
                return segments, plan_source[:80]
        log.warning("Vinted Radar catalog tree looks incomplete source=%s leaves=%s", source, len(leaves))
    if cached_scope == VINTED_RADAR_SCOPE and cached:
        kept = []
        seen: set[int] = set()
        for item in cached:
            try:
                cid = int(item.get("id") or 0)
            except Exception:
                cid = 0
            if cid > 0 and cid not in seen:
                seen.add(cid)
                kept.append((cid, str(item.get("name") or f"Catalog {cid}")[:255]))
        if kept:
            return kept[:VINTED_RADAR_MAX_SEGMENTS], "cached-balanced-market"
    return [], str(source or "unavailable")


def _rotated_categories(categories: list[tuple[int, str]], round_index: int) -> list[tuple[int, str]]:
    if not categories:
        return []
    # Shift the start by one category every round. Over time every category occupies
    # every position in the pass, so no catalog is permanently first or last.
    offset = max(0, int(round_index or 0)) % len(categories)
    return categories[offset:] + categories[:offset]


async def enable_radar(
    *, admin_user_id: int, categories: list[tuple[int, str]] | None = None,
    catalog_source: str = "", initial_scan_id: int | None = None, initial_scan_at: datetime | None = None,
) -> dict[str, Any]:
    now = utcnow()
    if not categories:
        categories, catalog_source = await resolve_all_market_categories(force=True)
    if not categories:
        raise RuntimeError("Vinted full catalog tree is temporarily unavailable")
    payload = {
        "enabled": True,
        "admin_user_id": int(admin_user_id),
        "scope": VINTED_RADAR_SCOPE,
        "catalog_source": str(catalog_source or "live")[:80],
        "categories": [{"id": int(cid), "name": str(name)[:255]} for cid, name in categories if int(cid) > 0],
        "pages": VINTED_RADAR_PAGES_PER_CATEGORY,
        "interval_minutes": VINTED_RADAR_INTERVAL_MINUTES,
        "last_scan_id": int(initial_scan_id or 0),
        "last_scan_at": (initial_scan_at or now).isoformat(timespec="seconds"),
        "rounds_started": 1 if initial_scan_id else 0,
        "updated_at": now.isoformat(timespec="seconds"),
    }
    await _save_setting(payload)
    invalidate_radar_cache()
    return await radar_config()


async def disable_radar() -> dict[str, Any]:
    cfg = await radar_config()
    raw = {
        "enabled": False,
        "admin_user_id": cfg["admin_user_id"],
        "scope": cfg.get("scope") or VINTED_RADAR_SCOPE,
        "catalog_source": cfg.get("catalog_source") or "",
        "categories": cfg["categories"],
        "pages": VINTED_RADAR_PAGES_PER_CATEGORY,
        "interval_minutes": cfg["interval_minutes"],
        "last_scan_id": int(cfg.get("last_scan_id") or 0),
        "last_scan_at": cfg["last_scan_at"].isoformat(timespec="seconds") if cfg.get("last_scan_at") else "",
        "rounds_started": int(cfg.get("rounds_started") or 0),
        "updated_at": utcnow().isoformat(timespec="seconds"),
    }
    await _save_setting(raw)
    invalidate_radar_cache()
    return await radar_config()


async def _latest_active_radar_scan() -> VintedScan | None:
    async with SessionLocal() as session:
        return (await session.execute(
            select(VintedScan)
            .where(VintedScan.mode == "radar", VintedScan.status.in_(tuple(_ACTIVE_SCAN_STATES)))
            .order_by(VintedScan.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()


async def cleanup_expired_radar_scans(*, max_scans: int = 4) -> int:
    """Bound Radar storage to the learning horizon without touching manual scans.

    Full-market 15-page snapshots can be large. We keep seven complete learning days
    plus one safety day, then delete old Radar children explicitly because these legacy
    tables intentionally do not rely on database FK cascades.
    """
    cutoff = utcnow() - timedelta(days=VINTED_RADAR_HISTORY_DAYS + 1)
    limit = max(1, min(24, int(max_scans or 4)))
    async with SessionLocal() as session:
        ids = list((await session.execute(
            select(VintedScan.id)
            .where(VintedScan.mode == "radar", VintedScan.created_at < cutoff)
            .order_by(VintedScan.created_at.asc())
            .limit(limit)
        )).scalars().all())
        if not ids:
            return 0
        await session.execute(delete(VintedMetricHistory).where(VintedMetricHistory.scan_id.in_(ids)))
        await session.execute(delete(VintedScanItem).where(VintedScanItem.scan_id.in_(ids)))
        await session.execute(delete(VintedScanCategory).where(VintedScanCategory.scan_id.in_(ids)))
        await session.execute(delete(VintedScan).where(VintedScan.id.in_(ids)))
        await session.commit()
        log.info("Vinted Radar retention cleanup removed scans=%s cutoff=%s", len(ids), cutoff.isoformat(timespec="seconds"))
        return len(ids)


async def maybe_start_due_round() -> VintedScan | None:
    cfg = await radar_config()
    if not cfg["enabled"] or not VINTED_QUEUE.enabled:
        return None
    scope_mismatch = str(cfg.get("scope") or "") != VINTED_RADAR_SCOPE
    active = await _latest_active_radar_scan()
    if active is not None:
        fresh = await recalc_scan(active.id)
        if fresh is not None and str(fresh.status or "") in _ACTIVE_SCAN_STATES:
            if scope_mismatch:
                # v4.23.3 migration: do not let a legacy thousands-of-leaves queue run
                # for hours after deploy. Queued categories are cancelled immediately;
                # already-running bounded tasks are allowed to finish safely.
                await cancel_scan(fresh.id)
                log.warning(
                    "Vinted Radar migration cancelled legacy scope scan=%s categories=%s scope=%s",
                    fresh.id, fresh.total_categories, cfg.get("scope"),
                )
            return None
    now = utcnow()
    # A scope migration starts a fresh optimized round as soon as the old one is gone;
    # it does not wait out the previous one-hour cadence.
    last_scan_at = None if scope_mismatch else cfg.get("last_scan_at")
    interval = timedelta(minutes=int(cfg["interval_minutes"]))
    if last_scan_at is not None and now < last_scan_at + interval:
        return None

    try:
        await cleanup_expired_radar_scans(max_scans=4)
    except Exception:
        log.exception("Vinted Radar retention cleanup failed")

    categories, source = await resolve_all_market_categories(
        force=True, cached=list(cfg.get("categories") or []), cached_scope=str(cfg.get("scope") or ""),
    )
    if not categories:
        log.warning("Vinted Radar full-market round skipped: catalog metadata unavailable")
        return None
    ordered = _rotated_categories(categories, int(cfg.get("rounds_started") or 0))
    scan = await create_scan(
        admin_user_id=int(cfg["admin_user_id"] or 0),
        mode="radar",
        categories=ordered,
        pages=VINTED_RADAR_PAGES_PER_CATEGORY,
    )
    try:
        queued = await enqueue_scan(scan.id)
        if queued <= 0:
            return None
    except Exception:
        log.exception("Vinted Radar 1.0 autoscan enqueue failed scan=%s", scan.id)
        return None
    payload = {
        "enabled": True,
        "admin_user_id": int(cfg["admin_user_id"] or 0),
        "scope": VINTED_RADAR_SCOPE,
        "catalog_source": source,
        "categories": [{"id": cid, "name": name} for cid, name in categories],
        "pages": VINTED_RADAR_PAGES_PER_CATEGORY,
        "interval_minutes": int(cfg["interval_minutes"]),
        "last_scan_id": int(scan.id),
        "last_scan_at": scan.created_at.isoformat(timespec="seconds"),
        "rounds_started": int(cfg.get("rounds_started") or 0) + 1,
        "updated_at": now.isoformat(timespec="seconds"),
    }
    await _save_setting(payload)
    invalidate_radar_cache()
    return scan


async def next_due_at() -> datetime | None:
    cfg = await radar_config()
    if not cfg["enabled"] or not cfg.get("last_scan_at"):
        return None
    return cfg["last_scan_at"] + timedelta(minutes=int(cfg["interval_minutes"]))


_cache_lock = asyncio.Lock()
_cache_value: tuple[float, VintedRadarSnapshot] | None = None
_cache_index: dict[int, VintedRadarEntry] = {}


def invalidate_radar_cache() -> None:
    global _cache_value, _cache_index
    _cache_value = None
    _cache_index = {}


async def radar_overview() -> dict[str, Any]:
    """Cheap UI status for Vinted Lab without rebuilding the seven-day Radar model.

    The full snapshot can contain a large multi-round item history and must not sit on
    the critical path of every Telegram button.  If a scored snapshot is already in
    memory we expose its counters; otherwise the home screen shows counters as pending
    while Radar itself remains available.
    """
    cfg = await radar_config()
    cached = _cache_value
    snapshot = cached[1] if cached is not None else None
    request_radar_snapshot_refresh(force=False)
    age_seconds = None if cached is None else max(0.0, time.monotonic() - cached[0])
    return {
        "enabled": bool(cfg.get("enabled")),
        "last_scan_id": cfg.get("last_scan_id"),
        "last_scan_at": cfg.get("last_scan_at"),
        "categories": list(cfg.get("categories") or []),
        "live_total": None if snapshot is None else int(snapshot.live_total),
        "hot": None if snapshot is None else int(snapshot.hot),
        "rising": None if snapshot is None else int(snapshot.rising),
        "deals": None if snapshot is None else int(snapshot.deals),
        "candidates": None if snapshot is None else int(snapshot.candidates),
        "followup_active": None if snapshot is None else int(snapshot.followup_active),
        "followup_due": None if snapshot is None else int(snapshot.followup_due),
        "followup_completed": None if snapshot is None else int(snapshot.followup_completed),
        "cache_age_seconds": age_seconds,
    }


def _coalesce_like_samples(samples: list[_Sample], *, min_gap_seconds: int = 600) -> list[_Sample]:
    """Collapse catalog/follow-up observations that land almost at the same moment.

    Without this, an hourly catalog sighting and an exact follow-up five minutes later
    could become the last two points and hide the real +30 -> +60 minute growth.
    """
    known = [sample for sample in samples if sample.item.catalog_favourite_count is not None]
    if not known:
        return []
    result: list[_Sample] = [known[0]]
    for sample in known[1:]:
        if (sample.scan_created_at - result[-1].scan_created_at).total_seconds() < max(300, int(min_gap_seconds)):
            result[-1] = sample
        else:
            result.append(sample)
    return result


def _discovery_absolute_like_points(likes: int | None) -> int:
    value = max(0, int(likes or 0))
    if value >= 20:
        return 25
    if value >= 10:
        return 22
    if value >= 5:
        return 18
    if value >= 3:
        return 14
    if value >= 1:
        return 8
    return 0


def _discovery_percentile_points(likes: int | None, percentile: float) -> int:
    if likes is None or int(likes) <= 0:
        return 0
    if percentile >= 0.95:
        return 25
    if percentile >= 0.90:
        return 22
    if percentile >= 0.75:
        return 16
    if percentile >= 0.50:
        return 10
    return 4


def _discovery_freshness_points(age_minutes: float) -> int:
    if age_minutes <= 30:
        return 10
    if age_minutes <= 60:
        return 7
    return 4



async def _followup_stats() -> dict[str, int]:
    now = utcnow()
    async with SessionLocal() as session:
        active = int((await session.execute(select(func.count(VintedRadarWatch.id)).where(
            VintedRadarWatch.status.in_(("watching", "queued", "processing"))
        ))).scalar_one() or 0)
        due = int((await session.execute(select(func.count(VintedRadarWatch.id)).where(
            VintedRadarWatch.status == "watching",
            VintedRadarWatch.next_check_at.is_not(None),
            VintedRadarWatch.next_check_at <= now,
        ))).scalar_one() or 0)
        completed = int((await session.execute(select(func.count(VintedRadarWatch.id)).where(
            VintedRadarWatch.status == "completed"
        ))).scalar_one() or 0)
    return {"active": active, "due": due, "completed": completed}


async def seed_followup_candidates() -> int:
    """Select a bounded fresh subset for targeted Like-Momentum observation.

    This is intentionally a *discovery* score, not the user-facing Vinted Score 100.
    It spends follow-up capacity on fresh items with unusual visible interest, useful
    pricing, scarcity or a small seller footprint without pretending that one sample
    already proves demand.
    """
    if not VINTED_RADAR_FOLLOWUP_ENABLED or not VINTED_QUEUE.enabled:
        return 0
    cfg = await radar_config()
    if not cfg.get("enabled"):
        # Stopping AutoScan stops new discovery, but already-created watches continue.
        return 0
    now = utcnow()
    cutoff = now - timedelta(minutes=VINTED_RADAR_FOLLOWUP_DISCOVERY_LOOKBACK_MINUTES)
    async with SessionLocal() as session:
        active = int((await session.execute(select(func.count(VintedRadarWatch.id)).where(
            VintedRadarWatch.status.in_(("watching", "queued", "processing"))
        ))).scalar_one() or 0)
        recent_created = int((await session.execute(select(func.count(VintedRadarWatch.id)).where(
            VintedRadarWatch.created_at >= now - timedelta(hours=1)
        ))).scalar_one() or 0)
        capacity = min(
            VINTED_RADAR_FOLLOWUP_MAX_ACTIVE - active,
            VINTED_RADAR_FOLLOWUP_MAX_NEW_PER_HOUR - recent_created,
            VINTED_RADAR_FOLLOWUP_SWEEP_LIMIT,
        )
        if capacity <= 0:
            return 0
        # Keep the seed query bounded. The newest 20k >=40 EUR rows are enough to
        # build local catalog percentiles while avoiding a seven-day full history scan.
        rows = list((await session.execute(
            select(
                VintedScanItem.scan_id, VintedScanItem.item_id, VintedScanItem.catalog_id,
                VintedScanItem.catalog_favourite_count, VintedScanItem.price_amount,
                VintedScanItem.brand, VintedScanItem.seller_id, VintedScanItem.created_at,
            )
            .join(VintedScan, VintedScan.id == VintedScanItem.scan_id)
            .where(
                VintedScan.mode == "radar",
                VintedScanItem.created_at >= cutoff,
                VintedScanItem.price_amount.is_not(None),
                VintedScanItem.price_amount >= VINTED_RADAR_MIN_PRICE_EUR,
                VintedScanItem.visible.is_not(False),
                VintedScanItem.promoted.is_not(True),
                ~VintedScan.status.in_(("failed", "cancelled")),
            )
            .order_by(VintedScanItem.created_at.desc())
            .limit(20000)
        )).all())
        if not rows:
            return 0

        # Latest fresh catalog observation per item.
        latest: dict[int, Any] = {}
        for row in rows:
            item_id = int(row[1])
            if item_id not in latest:
                latest[item_id] = row
        # Keep watch memory only for the learning horizon. This bounds both the table
        # and the de-duplication set while preventing the same item from being re-seeded
        # during its active/history window.
        await session.execute(delete(VintedRadarWatch).where(
            VintedRadarWatch.status.in_(("completed", "expired", "failed")),
            VintedRadarWatch.updated_at < now - timedelta(days=VINTED_RADAR_HISTORY_DAYS + 1),
        ))
        watched = set((await session.execute(select(VintedRadarWatch.item_id).where(
            VintedRadarWatch.updated_at >= now - timedelta(days=VINTED_RADAR_HISTORY_DAYS + 1)
        ))).scalars().all())

        catalog_likes: dict[int, list[int]] = {}
        catalog_prices: dict[int, list[float]] = {}
        brand_counts: dict[tuple[int, str], int] = {}
        seller_counts: dict[int, int] = {}
        for row in latest.values():
            catalog = int(row[2] or 0)
            likes = int(row[3]) if row[3] is not None else None
            price = _safe_float(row[4])
            brand = _norm(row[5])
            seller = int(row[6]) if row[6] is not None else None
            if catalog > 0 and likes is not None:
                catalog_likes.setdefault(catalog, []).append(likes)
            if catalog > 0 and price is not None and price >= VINTED_RADAR_MIN_PRICE_EUR:
                catalog_prices.setdefault(catalog, []).append(price)
            if catalog > 0 and brand:
                brand_counts[(catalog, brand)] = brand_counts.get((catalog, brand), 0) + 1
            if seller:
                seller_counts[seller] = seller_counts.get(seller, 0) + 1

        ranked: list[tuple[int, int, datetime, Any]] = []
        for item_id, row in latest.items():
            if item_id in watched:
                continue
            catalog = int(row[2] or 0)
            if catalog <= 0:
                continue
            likes = int(row[3]) if row[3] is not None else None
            price = _safe_float(row[4])
            brand = _norm(row[5])
            seller = int(row[6]) if row[6] is not None else None
            observed_at: datetime = row[7]
            age_minutes = max(0.0, (now - observed_at).total_seconds() / 60.0)
            like_pct = _percentile(likes, catalog_likes.get(catalog, []))
            prices = catalog_prices.get(catalog, [])
            median = _median(prices) if len(prices) >= VINTED_RADAR_MIN_PRICE_PEERS else None
            edge = None
            if price is not None and median is not None and median > 0:
                edge = (price - median) / median * 100.0
            score = (
                _discovery_absolute_like_points(likes)
                + _discovery_percentile_points(likes, like_pct)
                + _price_edge_points(edge)
                + _scarcity_points(brand_counts.get((catalog, brand), 0), brand)
                + _seller_points(seller_counts.get(seller, 0) if seller else 0, seller)
                + _discovery_freshness_points(age_minutes)
            )
            score = max(0, min(100, int(score)))
            if score < VINTED_RADAR_FOLLOWUP_MIN_DISCOVERY_SCORE:
                continue
            ranked.append((score, int(likes or 0), observed_at, row))

        ranked.sort(key=lambda x: (-x[0], -x[1], -x[2].timestamp()))
        inserted = 0
        for score, _likes_sort, observed_at, row in ranked[:capacity]:
            scan_id, item_id = int(row[0]), int(row[1])
            baseline_likes = int(row[3]) if row[3] is not None else None
            due = observed_at + timedelta(minutes=int(VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES[0]))
            session.add(VintedRadarWatch(
                item_id=item_id,
                source_scan_id=scan_id,
                discovery_score=int(score),
                baseline_likes=baseline_likes,
                last_likes=baseline_likes,
                first_seen_at=observed_at,
                status="watching",
                check_step=0,
                checks=0,
                exact_samples=0,
                retry_count=0,
                next_check_at=due,
                created_at=now,
                updated_at=now,
            ))
            inserted += 1
        if inserted:
            await session.commit()
            log.info("Vinted Radar follow-up seeded=%s active_before=%s recent_hour=%s", inserted, active, recent_created)
        return inserted


async def dispatch_due_followups() -> int:
    """Lease due watches in PostgreSQL and place only those checkpoints on Metrics Redis."""
    if not VINTED_RADAR_FOLLOWUP_ENABLED or not VINTED_QUEUE.enabled:
        return 0
    now = utcnow()
    async with SessionLocal() as session:
        # Retention is independent from AutoScan state so stopping discovery for days
        # cannot leave an ever-growing watch table behind.
        await session.execute(delete(VintedRadarWatch).where(
            VintedRadarWatch.status.in_(("completed", "expired", "failed")),
            VintedRadarWatch.updated_at < now - timedelta(days=VINTED_RADAR_HISTORY_DAYS + 1),
        ))
        # Crash self-heal: a lost worker/Redis message becomes eligible again after lease.
        stale = list((await session.execute(select(VintedRadarWatch).where(
            VintedRadarWatch.status.in_(("queued", "processing")),
            VintedRadarWatch.lease_until.is_not(None),
            VintedRadarWatch.lease_until < now,
        ).limit(VINTED_RADAR_FOLLOWUP_DISPATCH_BATCH))).scalars().all())
        for watch in stale:
            watch.status = "watching"
            watch.lease_until = None
            watch.next_check_at = now
            watch.updated_at = now
        # Watches are only useful during the 24h Live horizon.
        expired = list((await session.execute(select(VintedRadarWatch).where(
            VintedRadarWatch.status.in_(("watching", "queued", "processing")),
            VintedRadarWatch.first_seen_at < now - timedelta(hours=VINTED_RADAR_LIVE_HOURS),
        ).limit(VINTED_RADAR_FOLLOWUP_DISPATCH_BATCH))).scalars().all())
        for watch in expired:
            watch.status = "expired"
            watch.next_check_at = None
            watch.lease_until = None
            watch.updated_at = now
        # Commit retention and any state repair before leasing new work.
        await session.commit()

        due = list((await session.execute(select(VintedRadarWatch).where(
            VintedRadarWatch.status == "watching",
            VintedRadarWatch.next_check_at.is_not(None),
            VintedRadarWatch.next_check_at <= now,
        ).order_by(VintedRadarWatch.next_check_at.asc()).limit(VINTED_RADAR_FOLLOWUP_DISPATCH_BATCH))).scalars().all())
        if not due:
            return 0
        payloads: list[tuple[int, int, int, int]] = []
        for watch in due:
            watch.status = "queued"
            watch.lease_until = now + timedelta(seconds=VINTED_RADAR_FOLLOWUP_LEASE_SECONDS)
            watch.updated_at = now
            payloads.append((int(watch.id), int(watch.source_scan_id), int(watch.item_id), int(watch.check_step or 0)))
        await session.commit()

    queued = 0
    failed_ids: list[int] = []
    for watch_id, scan_id, item_id, step in payloads:
        try:
            result = await VINTED_QUEUE.enqueue_radar_followup(
                watch_id=watch_id, scan_id=scan_id, item_id=item_id, step=step,
            )
            if result in {"duplicate", ""}:
                # Existing marker/message owns the lease; do not create a second one.
                continue
            queued += 1
        except Exception:
            failed_ids.append(watch_id)
            log.exception("Vinted Radar follow-up enqueue failed watch=%s step=%s", watch_id, step)
    if failed_ids:
        async with SessionLocal() as session:
            rows = list((await session.execute(select(VintedRadarWatch).where(VintedRadarWatch.id.in_(failed_ids)))).scalars().all())
            for watch in rows:
                watch.status = "watching"
                watch.lease_until = None
                watch.next_check_at = utcnow() + timedelta(minutes=2)
                watch.updated_at = utcnow()
            await session.commit()
    return queued


_followup_last_seed_mono = 0.0


async def maintain_followup_lane() -> dict[str, int]:
    """One cheap scheduler tick: seed occasionally, dispatch due checkpoints always."""
    global _followup_last_seed_mono
    if not VINTED_RADAR_FOLLOWUP_ENABLED:
        return {"seeded": 0, "queued": 0}
    seeded = 0
    now_mono = time.monotonic()
    if now_mono - _followup_last_seed_mono >= VINTED_RADAR_FOLLOWUP_SEED_INTERVAL_SECONDS:
        _followup_last_seed_mono = now_mono
        try:
            seeded = await seed_followup_candidates()
        except Exception:
            log.exception("Vinted Radar follow-up discovery sweep failed")
    queued = await dispatch_due_followups()
    return {"seeded": int(seeded), "queued": int(queued)}


def _sample_rate(a: _Sample, b: _Sample) -> tuple[int | None, float | None, float | None]:
    a_likes = a.item.catalog_favourite_count
    b_likes = b.item.catalog_favourite_count
    if a_likes is None or b_likes is None:
        return None, None, None
    seconds = (b.scan_created_at - a.scan_created_at).total_seconds()
    if seconds < 300:
        return None, None, None
    delta = int(b_likes) - int(a_likes)
    if delta < 0:
        # Counter regressions are treated as an invalid interval, never as negative demand.
        return None, seconds / 3600.0, None
    hours = seconds / 3600.0
    return delta, hours, delta / hours if hours > 0 else None


def _median(values: Iterable[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v)) and float(v) > 0]
    if not clean:
        return None
    return float(statistics.median(clean))


def _status_for(
    score: int, sample_count: int, like_delta: int | None, price_edge_pct: float | None,
    *, likes: int | None, like_percentile: float, price_peer_count: int,
) -> str:
    movement = sample_count >= 2 and like_delta is not None and like_delta > 0
    # A cheap price by itself is not demand.  Deal needs a statistically useful
    # price cohort plus either confirmed movement or at least above-median visible
    # interest on the first catalog observation.
    strong_deal = (
        price_edge_pct is not None
        and price_edge_pct <= -25.0
        and int(price_peer_count) >= VINTED_RADAR_MIN_PRICE_PEERS
    )
    deal_interest = movement or (likes is not None and likes >= 2 and like_percentile >= 0.50)
    if movement and score >= 75:
        return "hot"
    if movement and score >= 58:
        return "rising"
    if strong_deal and deal_interest and score >= 40:
        return "deal"
    if score >= 35:
        return "candidate"
    return "baseline"


def _score_snapshot_rows(rows: list[Any], followup_rows: list[Any], now: datetime) -> tuple[list[VintedRadarEntry], int, dict[str, int]]:
    """CPU-heavy Radar scoring, intentionally executed outside the asyncio loop.

    Rows contain only scalar columns.  Keeping SQLAlchemy ORM objects and percentile /
    median loops off the Telegram event loop prevents Vinted Radar from stalling every
    other bot callback while a large seven-day history is scored.
    """
    grouped: dict[int, list[_Sample]] = {}
    for row in rows:
        row_price = _safe_float(row[5])
        if row_price is None or row_price < VINTED_RADAR_MIN_PRICE_EUR:
            continue
        item = _RadarItem(
            id=int(row[0]),
            scan_id=int(row[1]),
            item_id=int(row[2]),
            catalog_id=int(row[3]) if row[3] is not None else None,
            catalog_favourite_count=int(row[4]) if row[4] is not None else None,
            price_amount=row[5],
            brand=str(row[6] or ""),
            seller_id=int(row[7]) if row[7] is not None else None,
            title=str(row[8] or ""),
            url=str(row[9] or ""),
            category_name=str(row[10] or ""),
            currency=str(row[11] or ""),
            size=str(row[12] or ""),
            condition=str(row[13] or ""),
            seller_login=str(row[14] or ""),
        )
        scan_created_at = row[15]
        grouped.setdefault(item.item_id, []).append(
            _Sample(scan_id=item.scan_id, scan_created_at=scan_created_at, item=item)
        )

    # Targeted follow-up samples are identity-bound favourites from Metrics Worker.
    # They reuse catalog metadata but add measurements even after the listing has
    # shifted beyond the first 15 newest-first pages.
    followup_count = 0
    for row in followup_rows:
        item_id = int(row[1])
        samples = grouped.get(item_id)
        if not samples:
            continue
        base = samples[-1].item
        fav = int(row[3]) if row[3] is not None else None
        if fav is None:
            continue
        synthetic = _RadarItem(
            id=-int(row[0]),
            scan_id=int(row[2]),
            item_id=item_id,
            catalog_id=base.catalog_id,
            catalog_favourite_count=fav,
            price_amount=base.price_amount,
            brand=base.brand,
            seller_id=base.seller_id,
            title=base.title,
            url=base.url,
            category_name=base.category_name,
            currency=base.currency,
            size=base.size,
            condition=base.condition,
            seller_login=base.seller_login,
        )
        samples.append(_Sample(scan_id=int(row[2]), scan_created_at=row[4], item=synthetic))
        followup_count += 1

    # First pass: time-series primitives independent from peer statistics.
    primitives: dict[int, dict[str, Any]] = {}
    for item_id, samples in grouped.items():
        samples.sort(key=lambda sample: (sample.scan_created_at, sample.item.id))
        first_seen = samples[0].scan_created_at
        live_cutoff = first_seen + timedelta(hours=VINTED_RADAR_LIVE_HOURS)
        radar_samples = [sample for sample in samples if sample.scan_created_at <= live_cutoff]
        if not radar_samples:
            radar_samples = [samples[0]]
        latest = radar_samples[-1]
        latest_seen = latest.scan_created_at
        current_likes = latest.item.catalog_favourite_count
        known = _coalesce_like_samples(radar_samples)
        prev_likes: int | None = None
        like_delta: int | None = None
        sample_hours: float | None = None
        velocity: float | None = None
        acceleration: float | None = None
        if len(known) >= 2:
            prev_likes = int(known[-2].item.catalog_favourite_count)  # type: ignore[arg-type]
            like_delta, sample_hours, velocity = _sample_rate(known[-2], known[-1])
        if len(known) >= 3:
            _d1, _h1, rate1 = _sample_rate(known[-3], known[-2])
            _d2, _h2, rate2 = _sample_rate(known[-2], known[-1])
            if rate1 is not None and rate2 is not None:
                acceleration = rate2 - rate1
        sample_age = max(0.0, (latest_seen - first_seen).total_seconds() / 3600.0)
        current_age = max(0.0, (now - first_seen).total_seconds() / 3600.0)
        primitives[item_id] = {
            "samples": radar_samples,
            "latest": latest,
            "first_seen": first_seen,
            "last_seen": latest_seen,
            "current_age": current_age,
            "sample_age": sample_age,
            # Peer normalization must compare products by how long DT has known them
            # now, not by the duration between their first/last successful snapshots.
            "bucket": _age_bucket(min(current_age, 23.999)),
            "likes": int(current_likes) if current_likes is not None else None,
            "previous_likes": prev_likes,
            "like_delta": like_delta,
            "sample_hours": sample_hours,
            "velocity": velocity,
            "acceleration": acceleration,
            "sample_count": len(known),
        }

    live_ids = {
        item_id for item_id, primitive in primitives.items()
        if primitive["current_age"] <= float(VINTED_RADAR_LIVE_HOURS)
    }

    live_latest = [primitives[item_id]["latest"].item for item_id in live_ids]
    brand_counts: dict[tuple[int, str], int] = {}
    seller_counts: dict[int, int] = {}
    price_groups: dict[tuple[int, str], list[float]] = {}
    catalog_prices: dict[int, list[float]] = {}
    for item in live_latest:
        catalog = int(item.catalog_id or 0)
        key = (catalog, _norm(item.brand))
        if key[1]:
            brand_counts[key] = brand_counts.get(key, 0) + 1
        if item.seller_id:
            seller_counts[int(item.seller_id)] = seller_counts.get(int(item.seller_id), 0) + 1
        price = _safe_float(item.price_amount)
        # Unknown catalog ids must not collapse into one artificial mega-market.
        if catalog > 0 and price is not None and price >= VINTED_RADAR_MIN_PRICE_EUR:
            catalog_prices.setdefault(catalog, []).append(price)
            if key[1]:
                price_groups.setdefault(key, []).append(price)

    likes_ref: dict[tuple[int, str], list[int]] = {}
    velocity_ref: dict[tuple[int, str], list[float]] = {}
    brand_velocity_ref: dict[str, list[float]] = {}
    # Only current Live items belong in current peer percentiles.  The previous
    # implementation let expired 7-day learning rows vote in today's P50/P90.
    for item_id in live_ids:
        primitive = primitives[item_id]
        latest_item = primitive["latest"].item
        catalog = int(latest_item.catalog_id or 0)
        if catalog <= 0:
            continue
        bucket = str(primitive["bucket"])
        likes = primitive["likes"]
        velocity = primitive["velocity"]
        if likes is not None:
            likes_ref.setdefault((catalog, bucket), []).append(int(likes))
        if velocity is not None and velocity >= 0:
            velocity_ref.setdefault((catalog, bucket), []).append(float(velocity))
            brand = _norm(latest_item.brand)
            if brand:
                brand_velocity_ref.setdefault(brand, []).append(float(velocity))

    entries: list[VintedRadarEntry] = []
    for item_id in live_ids:
        primitive = primitives[item_id]
        latest_sample: _Sample = primitive["latest"]
        item = latest_sample.item
        catalog = int(item.catalog_id or 0)
        brand = _norm(item.brand)
        bucket = str(primitive["bucket"])
        likes = primitive["likes"]
        velocity = primitive["velocity"]
        like_pct = _percentile(likes, likes_ref.get((catalog, bucket), []))
        velocity_pct = _percentile(velocity, velocity_ref.get((catalog, bucket), []))

        price = _safe_float(item.price_amount)
        brand_price_peers = price_groups.get((catalog, brand), []) if catalog > 0 and brand else []
        catalog_price_peers = catalog_prices.get(catalog, []) if catalog > 0 else []
        if len(brand_price_peers) >= VINTED_RADAR_MIN_PRICE_PEERS:
            peers = brand_price_peers
        elif len(catalog_price_peers) >= VINTED_RADAR_MIN_PRICE_PEERS:
            peers = catalog_price_peers
        else:
            peers = []
        price_peer_count = len(peers)
        price_median = _median(peers) if price_peer_count >= VINTED_RADAR_MIN_PRICE_PEERS else None
        price_edge_pct = None
        if price is not None and price >= VINTED_RADAR_MIN_PRICE_EUR and price_median is not None and price_median > 0:
            price_edge_pct = (price - price_median) / price_median * 100.0

        scarcity_count = brand_counts.get((catalog, brand), 0) if brand else 0
        seller_active = seller_counts.get(int(item.seller_id), 0) if item.seller_id else 0
        brand_rates = brand_velocity_ref.get(brand, []) if brand else []
        brand_velocity = float(statistics.median(brand_rates)) if len(brand_rates) >= 2 else None

        components = {
            "like_velocity": _velocity_points(velocity, velocity_pct),
            "acceleration": _acceleration_points(primitive["acceleration"]),
            "price_edge": _price_edge_points(price_edge_pct),
            "likes_vs_peers": _peer_like_points(likes, like_pct),
            "scarcity": _scarcity_points(scarcity_count, brand),
            "seller": _seller_points(seller_active, item.seller_id),
            "brand_momentum": _brand_momentum_points(brand_velocity),
        }
        score = max(0, min(100, sum(components.values())))
        if int(primitive["sample_count"]) < 2:
            score = min(score, 59)
        status = _status_for(
            score, int(primitive["sample_count"]), primitive["like_delta"], price_edge_pct,
            likes=likes, like_percentile=like_pct, price_peer_count=price_peer_count,
        )
        entries.append(VintedRadarEntry(
            item_id=int(item_id),
            scan_id=int(latest_sample.scan_id),
            title=item.title[:500],
            url=item.url[:1200],
            catalog_id=catalog,
            category_name=item.category_name[:255],
            price_amount=price,
            currency=item.currency,
            brand=item.brand,
            size=item.size,
            condition=item.condition,
            seller_id=item.seller_id,
            seller_login=item.seller_login,
            first_seen_at=primitive["first_seen"],
            last_seen_at=primitive["last_seen"],
            age_hours=float(primitive["current_age"]),
            age_bucket=_age_bucket(float(primitive["current_age"])),
            likes=likes,
            previous_likes=primitive["previous_likes"],
            like_delta=primitive["like_delta"],
            sample_hours=primitive["sample_hours"],
            like_velocity=velocity,
            acceleration=primitive["acceleration"],
            price_median=price_median,
            price_edge_pct=price_edge_pct,
            price_peer_count=price_peer_count,
            like_percentile=like_pct,
            scarcity_count=scarcity_count,
            seller_active_count=seller_active,
            brand_velocity=brand_velocity,
            sample_count=int(primitive["sample_count"]),
            score=score,
            status=status,
            components=components,
        ))

    status_order = {"hot": 0, "rising": 1, "deal": 2, "candidate": 3, "baseline": 4}
    entries.sort(key=lambda entry: (status_order.get(entry.status, 9), -entry.score, -(entry.like_velocity or 0.0), -(entry.likes or 0)))
    counts = {name: sum(1 for entry in entries if entry.status == name) for name in ("hot", "rising", "deal", "candidate", "baseline")}
    counts["single_observation"] = sum(1 for entry in entries if entry.sample_count < 2)
    counts["repeat_observation"] = sum(1 for entry in entries if entry.sample_count >= 2)
    counts["positive_movement"] = sum(1 for entry in entries if entry.has_confirmed_movement)
    counts["followup_samples"] = int(followup_count)
    return entries, len(primitives), counts


async def _build_snapshot() -> VintedRadarSnapshot:
    now = utcnow()
    history_cutoff = now - timedelta(days=VINTED_RADAR_HISTORY_DAYS)
    async with SessionLocal() as session:
        # Fetch only the scalar columns the score actually needs.  The old ORM query
        # hydrated every VintedScanItem column for the whole seven-day history.
        rows = list((await session.execute(
            select(
                VintedScanItem.id,
                VintedScanItem.scan_id,
                VintedScanItem.item_id,
                VintedScanItem.catalog_id,
                VintedScanItem.catalog_favourite_count,
                VintedScanItem.price_amount,
                VintedScanItem.brand,
                VintedScanItem.seller_id,
                VintedScanItem.title,
                VintedScanItem.url,
                VintedScanItem.category_name,
                VintedScanItem.currency,
                VintedScanItem.size,
                VintedScanItem.condition,
                VintedScanItem.seller_login,
                # Actual page-persist time is the catalog measurement timestamp.
                # Using VintedScan.created_at made every item in a multi-hour full-market
                # round look as if it had been measured at the round start.
                VintedScanItem.created_at,
            )
            .join(VintedScan, VintedScan.id == VintedScanItem.scan_id)
            .where(
                VintedScan.mode == "radar",
                VintedScanItem.created_at >= history_cutoff,
                ~VintedScan.status.in_(("failed", "cancelled")),
                VintedScanItem.price_amount.is_not(None),
                VintedScanItem.price_amount >= VINTED_RADAR_MIN_PRICE_EUR,
                VintedScanItem.visible.is_not(False),
                VintedScanItem.promoted.is_not(True),
            )
            .order_by(VintedScanItem.created_at.asc(), VintedScanItem.id.asc())
        )).all())
        followup_rows = list((await session.execute(
            select(
                VintedMetricHistory.id,
                VintedMetricHistory.item_id,
                VintedMetricHistory.scan_id,
                VintedMetricHistory.favourite_count,
                VintedMetricHistory.measured_at,
            ).where(
                VintedMetricHistory.measured_at >= history_cutoff,
                VintedMetricHistory.identity_ok.is_(True),
                VintedMetricHistory.favourite_count.is_not(None),
                VintedMetricHistory.source.like("radar_followup%"),
            ).order_by(VintedMetricHistory.measured_at.asc(), VintedMetricHistory.id.asc())
        )).all())

    entries, history_items, counts = await asyncio.to_thread(_score_snapshot_rows, rows, followup_rows, now)
    followup_stats = await _followup_stats() if VINTED_RADAR_FOLLOWUP_ENABLED else {"active": 0, "due": 0, "completed": 0}
    cfg = await radar_config()
    last_scan_at = cfg.get("last_scan_at")
    next_scan_at = last_scan_at + timedelta(minutes=int(cfg["interval_minutes"])) if cfg["enabled"] and last_scan_at else None
    return VintedRadarSnapshot(
        generated_at=now,
        entries=entries,
        live_total=len(entries),
        hot=counts["hot"],
        rising=counts["rising"],
        deals=counts["deal"],
        candidates=counts["candidate"],
        baselines=counts["baseline"],
        single_observation=counts["single_observation"],
        repeat_observation=counts["repeat_observation"],
        positive_movement=counts["positive_movement"],
        followup_samples=counts.get("followup_samples", 0),
        followup_active=int(followup_stats.get("active", 0)),
        followup_due=int(followup_stats.get("due", 0)),
        followup_completed=int(followup_stats.get("completed", 0)),
        history_items=history_items,
        min_price_eur=float(VINTED_RADAR_MIN_PRICE_EUR),
        auto_enabled=bool(cfg["enabled"]),
        auto_interval_minutes=int(cfg["interval_minutes"]),
        last_scan_id=cfg.get("last_scan_id"),
        last_scan_at=last_scan_at,
        next_scan_at=next_scan_at,
        categories=list(cfg["categories"]),
        pages=int(cfg["pages"]),
    )


_refresh_task: asyncio.Task | None = None


def _snapshot_is_fresh(cached: tuple[float, VintedRadarSnapshot] | None, now_mono: float | None = None) -> bool:
    if cached is None:
        return False
    now_mono = time.monotonic() if now_mono is None else now_mono
    return now_mono - cached[0] <= VINTED_RADAR_CACHE_SECONDS


async def build_radar_snapshot(*, force: bool = False) -> VintedRadarSnapshot:
    global _cache_value, _cache_index
    cached = _cache_value
    if not force and _snapshot_is_fresh(cached):
        return cached[1]
    async with _cache_lock:
        cached = _cache_value
        if not force and _snapshot_is_fresh(cached):
            return cached[1]
        started = time.monotonic()
        snapshot = await _build_snapshot()
        _cache_index = {int(entry.item_id): entry for entry in snapshot.entries}
        _cache_value = (time.monotonic(), snapshot)
        log.info(
            "Vinted Radar snapshot rebuilt | rows/live_history=%s live=%s elapsed=%.2fs",
            snapshot.history_items, snapshot.live_total, time.monotonic() - started,
        )
        return snapshot


async def _background_refresh(force: bool = False) -> None:
    global _refresh_task
    try:
        await build_radar_snapshot(force=force)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Vinted Radar background snapshot refresh failed")
    finally:
        if _refresh_task is asyncio.current_task():
            _refresh_task = None


def request_radar_snapshot_refresh(*, force: bool = False) -> bool:
    """Start at most one background rebuild and return immediately to Telegram UI."""
    global _refresh_task
    cached = _cache_value
    if not force and _snapshot_is_fresh(cached):
        return False
    if _refresh_task is not None and not _refresh_task.done():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _refresh_task = loop.create_task(_background_refresh(force=force), name="vinted-radar-snapshot-refresh")
    return True


def peek_radar_snapshot() -> VintedRadarSnapshot | None:
    cached = _cache_value
    return cached[1] if cached is not None else None


async def get_radar_entry(item_id: int, *, force: bool = False) -> VintedRadarEntry | None:
    """Return the last completed entry instantly; stale refresh stays background-only."""
    target = int(item_id)
    if force:
        snapshot = await build_radar_snapshot(force=True)
        return _cache_index.get(target) or next((entry for entry in snapshot.entries if entry.item_id == target), None)
    cached = _cache_value
    if cached is None:
        request_radar_snapshot_refresh(force=False)
        return None
    request_radar_snapshot_refresh(force=False)
    return _cache_index.get(target)
