from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, text

from db import SessionLocal
from early_winner import opportunity_family_key
from filters import price_bounds
from models import (
    AIEarlyWinnerCandidate,
    AppSetting,
    Listing,
    RadarFavorite,
    RadarLifecycleWatch,
    RadarProduct,
    RadarProductListing,
    RadarSnapshot,
    ScanListing,
    UserScan,
)

log = logging.getLogger("dtparser-radar")

RADAR_BACKFILL_SETTING = "dt_radar_v1_backfill_complete"
RADAR_SCAN_TOP_LIMIT = 12
RADAR_PAGE_SIZE = 8

# v4.14.0 Fast Sold / Lifecycle. Strong fresh Radar listings are watched at
# absolute checkpoints after first discovery. A disappearance is never accepted
# from one miss: a second direct detail-page check confirms it a few minutes later.
RADAR_LIFECYCLE_MIN_SCORE = 72
RADAR_LIFECYCLE_CHECK_MINUTES = (15, 30, 60, 120, 180)
RADAR_LIFECYCLE_CONFIRM_MINUTES = 3
RADAR_LIFECYCLE_UNKNOWN_RETRY_MINUTES = 5
RADAR_LIFECYCLE_MAX_MINUTES = max(RADAR_LIFECYCLE_CHECK_MINUTES)
RADAR_FAST_SOLD_MAX_SECONDS = RADAR_LIFECYCLE_MAX_MINUTES * 60

_radar_lock = asyncio.Lock()


@dataclass(frozen=True)
class RadarStats:
    total: int
    hot: int
    rising: int
    ai_picks: int
    categories: int
    signals: int
    fast_sold: int = 0


@dataclass(frozen=True)
class LifecycleJob:
    id: int
    product_id: int
    external_id: str
    url: str
    first_seen_at: datetime
    last_seen_at: datetime
    status: str
    score: int
    check_step: int
    checks: int
    consecutive_missing: int


@dataclass(frozen=True)
class FastSoldInfo:
    product_id: int
    external_id: str
    title: str
    category_key: str
    disappeared_at: datetime
    confirmed_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    lifetime_seconds: int
    last_views: int | None
    last_price_eur: int | None
    peak_score: int


def _clamp_score(value: int | float) -> int:
    return max(0, min(100, int(round(float(value)))))


def radar_product_key(listing: Listing, cohort_key: str | None = None) -> str:
    """Stable family key shared by scan TOPs and the AI worker."""
    if cohort_key:
        value = str(cohort_key).strip()
        if value:
            return value[:600]
    if listing.identity_key and int(listing.identity_confidence or 0) >= 70:
        return f"id:{listing.identity_key}"[:600]
    family = opportunity_family_key(str(listing.title or ""), str(listing.category_key or "unknown"))
    if family:
        return family[:600]
    return f"listing:{listing.external_id}"[:600]


def _status_for_score(score: int) -> str:
    if score >= 85:
        return "hot"
    if score >= 72:
        return "rising"
    if score >= 58:
        return "stable"
    if score >= 38:
        return "cooling"
    return "historical"


def _effective_score(product: RadarProduct, now: datetime) -> int:
    """Fresh signals rise; old signals cool but never disappear from Radar."""
    raw = int(product.last_signal_score or product.current_score or 0)
    if product.last_signal_at is None:
        return _clamp_score(raw)
    age_hours = max(0.0, (now - product.last_signal_at).total_seconds() / 3600.0)
    # Give a signal three full days before cooling. Afterwards the live score loses
    # two points/day, while repeatability/confirmed evidence contributes a small,
    # bounded durable bonus. Peak score is never reduced.
    decay = max(0.0, age_hours - 72.0) / 24.0 * 2.0
    repeat_bonus = min(6.0, math.log2(max(1, int(product.signal_count or 0)) + 1) * 1.5)
    confirmed_bonus = min(6.0, float(int(product.confirmed_count or 0)) * 2.0)
    return _clamp_score(raw + repeat_bonus + confirmed_bonus - decay)


def _next_lifecycle_checkpoint(first_seen_at: datetime, now: datetime) -> tuple[int, datetime] | None:
    elapsed = max(0.0, (now - first_seen_at).total_seconds() / 60.0)
    for step, minutes in enumerate(RADAR_LIFECYCLE_CHECK_MINUTES):
        if elapsed < float(minutes):
            return step, first_seen_at + timedelta(minutes=int(minutes))
    return None


async def _maybe_queue_lifecycle_watch(
    session, *, product: RadarProduct, listing: Listing, score: int, now: datetime
) -> None:
    """Enroll a fresh strong listing in the durable Lifecycle queue.

    The helper runs inside the same transaction as the Radar signal. Existing
    watches are only refreshed; disappeared/expired history is never resurrected.
    """
    if int(score or 0) < RADAR_LIFECYCLE_MIN_SCORE:
        return
    if not bool(listing.is_active) or not str(listing.url or "").strip():
        return
    first_seen = listing.first_seen_at or now
    checkpoint = _next_lifecycle_checkpoint(first_seen, now)
    if checkpoint is None:
        return
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(CAST(hashtext(:lifecycle_key) AS bigint))"),
            {"lifecycle_key": f"lifecycle:{listing.external_id}"},
        )
    existing = (await session.execute(
        select(RadarLifecycleWatch).where(
            RadarLifecycleWatch.external_id == str(listing.external_id)
        ).limit(1)
    )).scalar_one_or_none()
    tier = "A" if int(score or 0) >= 85 else "B"
    if existing is None:
        step, next_check = checkpoint
        session.add(RadarLifecycleWatch(
            product_id=int(product.id),
            external_id=str(listing.external_id),
            category_key=str(listing.category_key or ""),
            title=str(listing.identity_label or listing.title or "")[:500],
            url=str(listing.url or "")[:1200],
            first_seen_at=first_seen,
            radar_started_at=now,
            last_seen_at=listing.last_seen_at or now,
            status="watching",
            tier=tier,
            score=int(score or 0),
            peak_score=int(score or 0),
            last_views=(int(listing.view_count) if listing.view_count is not None else None),
            last_price_eur=listing.price_eur,
            check_step=int(step),
            next_check_at=next_check,
            created_at=now,
            updated_at=now,
        ))
        log.info(
            "DT Radar Lifecycle queued external_id=%s product=%s score=%s next=%s",
            listing.external_id, product.id, score, next_check.isoformat(timespec="seconds"),
        )
        return
    if str(existing.status or "") in {"disappeared", "expired"}:
        return
    existing.product_id = int(product.id)
    existing.category_key = str(listing.category_key or existing.category_key or "")
    existing.title = str(listing.identity_label or listing.title or existing.title or "")[:500]
    existing.url = str(listing.url or existing.url or "")[:1200]
    existing.score = max(int(existing.score or 0), int(score or 0))
    existing.peak_score = max(int(existing.peak_score or 0), int(score or 0))
    existing.tier = "A" if int(existing.peak_score or 0) >= 85 else "B"
    existing.last_views = max(int(existing.last_views or 0), int(listing.view_count or 0)) if listing.view_count is not None else existing.last_views
    existing.last_price_eur = listing.price_eur if listing.price_eur is not None else existing.last_price_eur
    existing.last_seen_at = max(existing.last_seen_at or now, listing.last_seen_at or now)
    existing.updated_at = now


async def _upsert_signal(
    *,
    source_key: str,
    source: str,
    listing: Listing,
    product_key: str,
    score: int,
    confidence: int = 0,
    stage: str = "",
    outcome: str = "",
    opportunity_type: str = "spark",
    scan_id: int | None = None,
    candidate_id: int | None = None,
    view_count: int | None = None,
    views_per_hour: float | None = None,
    reasons: list[str] | tuple[str, ...] | None = None,
    recorded_at: datetime | None = None,
) -> int | None:
    """Append one idempotent Radar snapshot and refresh its aggregate product."""
    now = recorded_at or datetime.utcnow()
    score = _clamp_score(score)
    confidence = _clamp_score(confidence)
    reason_list = [str(x) for x in (reasons or []) if str(x).strip()]
    latest_reason = (reason_list[0] if reason_list else "")[:800]

    async with _radar_lock:
        async with SessionLocal() as session:
            # Main Bot and AI Worker are separate Railway processes. Serialize only
            # writes for the same product family so both can safely discover a new
            # Radar product at the same time without a unique-key race.
            bind = session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(CAST(hashtext(:radar_key) AS bigint))"),
                    {"radar_key": product_key},
                )
            duplicate = (await session.execute(
                select(RadarSnapshot.id).where(RadarSnapshot.source_key == source_key).limit(1)
            )).scalar_one_or_none()
            if duplicate is not None:
                return None

            product = (await session.execute(
                select(RadarProduct).where(RadarProduct.product_key == product_key).limit(1)
            )).scalar_one_or_none()
            if product is None:
                product = RadarProduct(
                    product_key=product_key,
                    category_key=str(listing.category_key or ""),
                    title=str(listing.identity_label or listing.title or "")[:500],
                    representative_external_id=str(listing.external_id),
                    first_seen_at=listing.first_seen_at or now,
                    last_seen_at=listing.last_seen_at or now,
                    first_radar_at=now,
                    last_signal_at=now,
                    last_signal_score=score,
                    current_score=score,
                    peak_score=score,
                    confidence=confidence,
                    status=_status_for_score(score),
                    opportunity_type=(opportunity_type or "spark")[:32],
                    signal_count=0,
                    confirmed_count=0,
                    listing_count=0,
                    best_views=max(0, int(view_count or 0)),
                    best_views_per_hour=max(0.0, float(views_per_hour or 0.0)),
                    min_price_eur=listing.price_eur,
                    max_price_eur=listing.price_eur,
                    latest_reason=latest_reason,
                    latest_source=source[:32],
                    last_ai_candidate_id=candidate_id,
                    updated_at=now,
                )
                session.add(product)
                await session.flush()

            assoc = (await session.execute(
                select(RadarProductListing).where(
                    RadarProductListing.product_id == int(product.id),
                    RadarProductListing.external_id == str(listing.external_id),
                ).limit(1)
            )).scalar_one_or_none()
            if assoc is None:
                assoc = RadarProductListing(
                    product_id=int(product.id),
                    external_id=str(listing.external_id),
                    first_seen_at=listing.first_seen_at or now,
                    last_seen_at=listing.last_seen_at or now,
                    best_views=max(0, int(view_count or 0)),
                    last_price_eur=listing.price_eur,
                )
                session.add(assoc)
                product.listing_count = int(product.listing_count or 0) + 1
            else:
                assoc.last_seen_at = max(assoc.last_seen_at or now, listing.last_seen_at or now)
                assoc.best_views = max(int(assoc.best_views or 0), int(view_count or 0))
                assoc.last_price_eur = listing.price_eur

            was_confirmed = False
            if candidate_id is not None and outcome == "confirmed":
                was_confirmed = bool((await session.execute(
                    select(RadarSnapshot.id).where(
                        RadarSnapshot.product_id == int(product.id),
                        RadarSnapshot.candidate_id == int(candidate_id),
                        RadarSnapshot.outcome == "confirmed",
                    ).limit(1)
                )).scalar_one_or_none())

            session.add(RadarSnapshot(
                source_key=source_key[:160],
                product_id=int(product.id),
                external_id=str(listing.external_id),
                scan_id=scan_id,
                candidate_id=candidate_id,
                source=source[:32],
                score=score,
                confidence=confidence,
                stage=stage[:24],
                outcome=outcome[:24],
                opportunity_type=(opportunity_type or "")[:32],
                view_count=view_count,
                views_per_hour=views_per_hour,
                price_eur=listing.price_eur,
                reasons_json=json.dumps(reason_list, ensure_ascii=False),
                recorded_at=now,
            ))

            product.signal_count = int(product.signal_count or 0) + 1
            if candidate_id is not None and outcome == "confirmed" and not was_confirmed:
                product.confirmed_count = int(product.confirmed_count or 0) + 1
            product.category_key = str(listing.category_key or product.category_key or "")
            if listing.identity_label:
                product.title = str(listing.identity_label)[:500]
            elif not product.title:
                product.title = str(listing.title or "")[:500]
            product.representative_external_id = str(listing.external_id)
            product.first_seen_at = min(product.first_seen_at or now, listing.first_seen_at or now)
            product.last_seen_at = max(product.last_seen_at or now, listing.last_seen_at or now)
            if now >= (product.last_signal_at or now):
                product.last_signal_at = now
                product.last_signal_score = score
                product.confidence = max(int(product.confidence or 0), confidence)
                product.opportunity_type = (opportunity_type or product.opportunity_type or "spark")[:32]
                product.latest_reason = latest_reason or product.latest_reason
                product.latest_source = source[:32]
                if candidate_id is not None:
                    product.last_ai_candidate_id = int(candidate_id)
            product.best_views = max(int(product.best_views or 0), int(view_count or 0))
            product.best_views_per_hour = max(float(product.best_views_per_hour or 0.0), float(views_per_hour or 0.0))
            if listing.price_eur is not None:
                price = int(listing.price_eur)
                product.min_price_eur = price if product.min_price_eur is None else min(int(product.min_price_eur), price)
                product.max_price_eur = price if product.max_price_eur is None else max(int(product.max_price_eur), price)
            product.peak_score = max(int(product.peak_score or 0), score)

            # Product-level live score is based on the newest signal for each
            # distinct listing, then takes the strongest currently observed listing.
            # A later lower AI checkpoint can therefore cool one listing, while a
            # second independently strong listing can keep the product family hot.
            await session.flush()
            recent_snapshots = list((await session.execute(
                select(RadarSnapshot)
                .where(RadarSnapshot.product_id == int(product.id))
                .order_by(RadarSnapshot.recorded_at.desc(), RadarSnapshot.id.desc())
                .limit(300)
            )).scalars().all())
            latest_by_listing: dict[str, RadarSnapshot] = {}
            for snap in recent_snapshots:
                ext = str(snap.external_id or f"snapshot:{snap.id}")
                if ext not in latest_by_listing:
                    latest_by_listing[ext] = snap
            if latest_by_listing:
                product.last_signal_score = max(int(x.score or 0) for x in latest_by_listing.values())
                product.last_signal_at = max(x.recorded_at for x in latest_by_listing.values() if x.recorded_at is not None)
            product.current_score = _effective_score(product, now)
            product.status = _status_for_score(int(product.current_score or 0))
            product.updated_at = now
            await _maybe_queue_lifecycle_watch(
                session, product=product, listing=listing, score=score, now=now
            )
            await session.commit()
            return int(product.id)


async def record_scan_hot(scan_id: int, limit: int = RADAR_SCAN_TOP_LIMIT) -> int:
    """Merge the scan's TOP-by-real-views into the global persistent Radar."""
    async with SessionLocal() as session:
        scan = await session.get(UserScan, int(scan_id))
        if scan is None or scan.status != "done" or not scan.target_complete:
            return 0
        pairs = (await session.execute(
            select(Listing, ScanListing)
            .join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(ScanListing.scan_id == int(scan_id), Listing.is_promoted.is_(False))
        )).all()
    ranked = [(listing, snap) for listing, snap in pairs if snap.initial_view_count is not None]
    ranked.sort(key=lambda item: (int(item[1].initial_view_count or 0), item[0].first_seen_at), reverse=True)
    ranked = ranked[: max(1, int(limit))]
    if not ranked:
        return 0
    saved = 0
    n = len(ranked)
    for index, (listing, snap) in enumerate(ranked):
        percentile = 1.0 if n == 1 else 1.0 - (index / max(1, n - 1))
        views = max(0, int(snap.initial_view_count or 0))
        view_bonus = min(8, int(round(math.log10(max(1, views) + 1) * 2.5)))
        score = _clamp_score(58 + percentile * 20 + view_bonus)
        result = await _upsert_signal(
            source_key=f"scan-hot:{scan_id}:{listing.external_id}",
            source="scan_hot",
            listing=listing,
            product_key=radar_product_key(listing),
            score=score,
            confidence=55,
            stage="rising" if score >= 72 else "watch",
            opportunity_type="hot_product" if score >= 82 else "spark",
            scan_id=int(scan_id),
            view_count=views,
            reasons=[f"TOP-{index + 1} по реальным просмотрам в завершённом скане"],
            recorded_at=snap.captured_at,
        )
        if result is not None:
            saved += 1
    if saved:
        log.info("DT Radar scan merge scan=%s products=%s", scan_id, saved)
    return saved


async def record_autoscan_hot(
    round_id: str,
    category_key: str,
    matched_ids: list[str] | tuple[str, ...] | set[str],
    *,
    limit: int = RADAR_SCAN_TOP_LIMIT,
) -> int:
    """Merge one Radar AutoScan category into the persistent global Radar.

    AutoScan is intentionally not represented as a fake UserScan.  The crawler
    writes normal Listing/ViewHistory rows, then this helper promotes only the
    strongest verified-view listings into Radar, exactly like completed user
    scans do.  ``source_key`` contains the round id, so retries/restarts are
    idempotent and cannot duplicate a Radar signal.
    """
    ids = [str(x).strip() for x in matched_ids if str(x).strip()]
    if not ids:
        return 0
    # Preserve order-independent uniqueness without generating an unbounded IN.
    ids = list(dict.fromkeys(ids))[:5000]
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(Listing).where(
                Listing.external_id.in_(ids),
                Listing.category_key == str(category_key),
                Listing.is_promoted.is_(False),
                Listing.view_count.is_not(None),
            )
        )).scalars().all())
    rows.sort(
        key=lambda listing: (int(listing.view_count or 0), listing.first_seen_at or datetime.min),
        reverse=True,
    )
    ranked = rows[: max(1, int(limit))]
    if not ranked:
        return 0

    saved = 0
    n = len(ranked)
    clean_round = str(round_id or "round").replace(":", "-")[:48]
    for index, listing in enumerate(ranked):
        percentile = 1.0 if n == 1 else 1.0 - (index / max(1, n - 1))
        views = max(0, int(listing.view_count or 0))
        view_bonus = min(8, int(round(math.log10(max(1, views) + 1) * 2.5)))
        score = _clamp_score(58 + percentile * 20 + view_bonus)
        result = await _upsert_signal(
            source_key=f"autoscan:{clean_round}:{listing.external_id}",
            source="radar_autoscan",
            listing=listing,
            product_key=radar_product_key(listing),
            score=score,
            confidence=55,
            stage="rising" if score >= 72 else "watch",
            opportunity_type="hot_product" if score >= 82 else "spark",
            view_count=views,
            reasons=[f"TOP-{index + 1} по реальным просмотрам в автокруге DT Radar"],
            recorded_at=listing.views_checked_at or listing.last_seen_at or datetime.utcnow(),
        )
        if result is not None:
            saved += 1
    if saved:
        log.info(
            "DT Radar autoscan merge round=%s category=%s products=%s candidates=%s",
            clean_round, category_key, saved, len(ranked),
        )
    return saved


async def record_ai_candidate(candidate_id: int, *, source_key: str | None = None, source: str = "ai") -> int | None:
    """Merge the latest AI state into Radar. Control candidates never enter Radar."""
    async with SessionLocal() as session:
        candidate = await session.get(AIEarlyWinnerCandidate, int(candidate_id))
        if candidate is None or candidate.is_control:
            return None
        listing = (await session.execute(
            select(Listing).where(Listing.external_id == candidate.external_id).limit(1)
        )).scalar_one_or_none()
        if listing is None:
            return None
        try:
            reasons = json.loads(candidate.latest_reasons_json or candidate.reasons_json or "[]")
            if not isinstance(reasons, list):
                reasons = []
        except Exception:
            reasons = []
        key = radar_product_key(listing, candidate.cohort_key)
        vph = None
        if candidate.latest_at and candidate.baseline_at and int(candidate.latest_views or 0) >= int(candidate.baseline_views or 0):
            hours = max(0.25, (candidate.latest_at - candidate.baseline_at).total_seconds() / 3600.0)
            vph = max(0.0, (int(candidate.latest_views or 0) - int(candidate.baseline_views or 0)) / hours)
        recorded_at = candidate.latest_at or candidate.created_at or datetime.utcnow()
        return await _upsert_signal(
            source_key=source_key or f"ai-state:{candidate.id}:{int(recorded_at.timestamp())}",
            source=source,
            listing=listing,
            product_key=key,
            score=int(candidate.current_score or candidate.initial_score or 0),
            confidence=int(candidate.confidence or 0),
            stage=str(candidate.stage or ""),
            outcome=str(candidate.outcome or ""),
            opportunity_type=str(candidate.opportunity_type or "spark"),
            scan_id=int(candidate.scan_id),
            candidate_id=int(candidate.id),
            view_count=int(candidate.latest_views or candidate.baseline_views or 0),
            views_per_hour=vph if vph is not None else float(candidate.initial_views_per_hour or 0.0),
            reasons=[str(x) for x in reasons],
            recorded_at=recorded_at,
        )


async def claim_due_lifecycle_watches(
    worker_id: str, *, limit: int = 20, lease_seconds: int = 180
) -> list[LifecycleJob]:
    """Atomically lease due Lifecycle checks from PostgreSQL/SQLite."""
    now = datetime.utcnow()
    async with SessionLocal() as session:
        query = (
            select(RadarLifecycleWatch)
            .where(
                RadarLifecycleWatch.status.in_(["watching", "confirming"]),
                RadarLifecycleWatch.next_check_at.is_not(None),
                RadarLifecycleWatch.next_check_at <= now,
                (RadarLifecycleWatch.lease_until.is_(None)) | (RadarLifecycleWatch.lease_until < now),
            )
            .order_by(RadarLifecycleWatch.next_check_at.asc(), RadarLifecycleWatch.id.asc())
            .limit(max(1, min(100, int(limit))))
        )
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        rows = list((await session.execute(query)).scalars().all())
        lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
        jobs: list[LifecycleJob] = []
        for row in rows:
            row.lease_owner = str(worker_id or "lifecycle")[:120]
            row.lease_until = lease_until
            row.updated_at = now
            jobs.append(LifecycleJob(
                id=int(row.id), product_id=int(row.product_id),
                external_id=str(row.external_id), url=str(row.url or ""),
                first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at,
                status=str(row.status or "watching"), score=int(row.score or 0),
                check_step=int(row.check_step or 0), checks=int(row.checks or 0),
                consecutive_missing=int(row.consecutive_missing or 0),
            ))
        if rows:
            await session.commit()
        return jobs


def _lifecycle_reason(lifetime_seconds: int) -> str:
    minutes = max(1, int(round(max(0, lifetime_seconds) / 60.0)))
    return f"Объявление исчезло примерно через {minutes} мин после первого обнаружения DT Radar"


async def complete_lifecycle_check(
    watch_id: int, active: bool | None, *, error_text: str | None = None, checked_at: datetime | None = None
) -> str:
    """Persist one direct availability result. Returns the new watch status."""
    now = checked_at or datetime.utcnow()
    async with SessionLocal() as session:
        query = select(RadarLifecycleWatch).where(RadarLifecycleWatch.id == int(watch_id)).limit(1)
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update()
        watch = (await session.execute(query)).scalar_one_or_none()
        if watch is None:
            return "missing"
        if str(watch.status or "") not in {"watching", "confirming"}:
            watch.lease_owner = ""
            watch.lease_until = None
            await session.commit()
            return str(watch.status or "unknown")

        watch.checks = int(watch.checks or 0) + 1
        watch.last_checked_at = now
        watch.last_error = (str(error_text)[:1000] if error_text else None)
        watch.lease_owner = ""
        watch.lease_until = None
        watch.updated_at = now

        listing = (await session.execute(
            select(Listing).where(Listing.external_id == str(watch.external_id)).limit(1)
        )).scalar_one_or_none()

        if active is True:
            watch.status = "watching"
            watch.last_result = "active"
            watch.consecutive_missing = 0
            watch.first_missing_at = None
            watch.last_seen_at = now
            if listing is not None:
                listing.is_active = True
                listing.disappeared_at = None
                listing.last_seen_at = max(listing.last_seen_at or now, now)
                if listing.view_count is not None:
                    watch.last_views = max(int(watch.last_views or 0), int(listing.view_count or 0))
                if listing.price_eur is not None:
                    watch.last_price_eur = int(listing.price_eur)
            checkpoint = _next_lifecycle_checkpoint(watch.first_seen_at, now)
            if checkpoint is None:
                watch.status = "expired"
                watch.next_check_at = None
                watch.last_result = "active_at_3h"
            else:
                step, next_check = checkpoint
                watch.check_step = int(step)
                watch.next_check_at = next_check

        elif active is False:
            watch.last_result = "unavailable"
            if int(watch.consecutive_missing or 0) <= 0:
                # One miss is only a candidate. A second direct detail-page miss
                # after a short delay is required before Fast Sold is recorded.
                watch.consecutive_missing = 1
                watch.first_missing_at = now
                watch.status = "confirming"
                watch.next_check_at = now + timedelta(minutes=RADAR_LIFECYCLE_CONFIRM_MINUTES)
            else:
                disappeared_at = watch.first_missing_at or now
                lifetime_seconds = max(0, int((disappeared_at - watch.first_seen_at).total_seconds()))
                watch.status = "disappeared"
                watch.consecutive_missing = 2
                watch.disappeared_at = disappeared_at
                watch.confirmed_at = now
                watch.lifetime_seconds = lifetime_seconds
                watch.next_check_at = None
                watch.last_result = "confirmed_disappeared"
                if listing is not None:
                    listing.is_active = False
                    listing.disappeared_at = disappeared_at
                product = await session.get(RadarProduct, int(watch.product_id))
                if product is not None:
                    reason = _lifecycle_reason(lifetime_seconds)
                    duplicate = (await session.execute(
                        select(RadarSnapshot.id).where(
                            RadarSnapshot.source_key == f"lifecycle-fast:{int(watch.id)}"
                        ).limit(1)
                    )).scalar_one_or_none()
                    if duplicate is None:
                        score = max(int(product.current_score or 0), int(watch.peak_score or watch.score or 0))
                        session.add(RadarSnapshot(
                            source_key=f"lifecycle-fast:{int(watch.id)}",
                            product_id=int(product.id),
                            external_id=str(watch.external_id),
                            source="lifecycle",
                            score=_clamp_score(score),
                            confidence=max(55, int(product.confidence or 0)),
                            stage="fast_sold", outcome="disappeared",
                            opportunity_type="fast_sold",
                            view_count=watch.last_views,
                            price_eur=watch.last_price_eur,
                            reasons_json=json.dumps([reason], ensure_ascii=False),
                            recorded_at=now,
                        ))
                        product.signal_count = int(product.signal_count or 0) + 1
                    product.latest_reason = reason[:800]
                    product.latest_source = "lifecycle"
                    product.last_signal_at = max(product.last_signal_at or now, now)
                    product.updated_at = now
                log.info(
                    "DT Radar Fast Sold confirmed external_id=%s product=%s lifetime=%ss checks=%s",
                    watch.external_id, watch.product_id, lifetime_seconds, watch.checks,
                )

        else:
            watch.last_result = "unknown"
            # Refusals/timeouts never count as disappearance. Retry gently. If the
            # 3-hour horizon has already passed, one small grace period is enough.
            elapsed_minutes = max(0.0, (now - watch.first_seen_at).total_seconds() / 60.0)
            if elapsed_minutes > RADAR_LIFECYCLE_MAX_MINUTES + 15:
                watch.status = "expired"
                watch.next_check_at = None
            else:
                watch.next_check_at = now + timedelta(minutes=RADAR_LIFECYCLE_UNKNOWN_RETRY_MINUTES)

        await session.commit()
        return str(watch.status or "unknown")


async def get_fast_sold_infos(product_ids: list[int] | tuple[int, ...]) -> dict[int, FastSoldInfo]:
    ids = list(dict.fromkeys(int(x) for x in product_ids if int(x) > 0))
    if not ids:
        return {}
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(RadarLifecycleWatch).where(
                RadarLifecycleWatch.product_id.in_(ids),
                RadarLifecycleWatch.status == "disappeared",
                RadarLifecycleWatch.lifetime_seconds.is_not(None),
                RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
            ).order_by(
                RadarLifecycleWatch.disappeared_at.desc(), RadarLifecycleWatch.lifetime_seconds.asc()
            )
        )).scalars().all())
    result: dict[int, FastSoldInfo] = {}
    for row in rows:
        product_id = int(row.product_id)
        if product_id in result or row.disappeared_at is None or row.lifetime_seconds is None:
            continue
        result[product_id] = FastSoldInfo(
            product_id=product_id, external_id=str(row.external_id),
            title=str(row.title or ""), category_key=str(row.category_key or ""),
            disappeared_at=row.disappeared_at, confirmed_at=row.confirmed_at,
            first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at,
            lifetime_seconds=int(row.lifetime_seconds), last_views=row.last_views,
            last_price_eur=row.last_price_eur, peak_score=int(row.peak_score or row.score or 0),
        )
    return result


async def get_fast_sold_info(product_id: int) -> FastSoldInfo | None:
    return (await get_fast_sold_infos([int(product_id)])).get(int(product_id))


async def lifecycle_queue_stats() -> dict[str, int]:
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(RadarLifecycleWatch.status, func.count(RadarLifecycleWatch.id)).group_by(RadarLifecycleWatch.status)
        )).all()
    stats = {str(status): int(count or 0) for status, count in rows}
    stats["total"] = sum(stats.values())
    return stats


async def refresh_radar_scores() -> int:
    """Apply age cooling to current score without deleting historical products."""
    now = datetime.utcnow()
    changed = 0
    async with _radar_lock:
        async with SessionLocal() as session:
            products = list((await session.execute(
                select(RadarProduct).where(
                    (RadarProduct.status != "historical") | (RadarProduct.current_score > 25)
                )
            )).scalars().all())
            for product in products:
                new_score = _effective_score(product, now)
                new_status = _status_for_score(new_score)
                if int(product.current_score or 0) != new_score or product.status != new_status:
                    product.current_score = new_score
                    product.status = new_status
                    product.updated_at = now
                    changed += 1
            if changed:
                await session.commit()
    return changed


async def radar_stats() -> RadarStats:
    async with SessionLocal() as session:
        total = int((await session.execute(select(func.count(RadarProduct.id)))).scalar_one() or 0)
        hot = int((await session.execute(select(func.count(RadarProduct.id)).where(RadarProduct.status == "hot"))).scalar_one() or 0)
        rising = int((await session.execute(select(func.count(RadarProduct.id)).where(RadarProduct.status == "rising"))).scalar_one() or 0)
        ai_picks = int((await session.execute(select(func.count(RadarProduct.id)).where(
            RadarProduct.opportunity_type.in_(["hot_product", "hidden_gem", "emerging"]),
            RadarProduct.confidence >= 55,
        ))).scalar_one() or 0)
        categories = int((await session.execute(select(func.count(func.distinct(RadarProduct.category_key))))).scalar_one() or 0)
        signals = int((await session.execute(select(func.count(RadarSnapshot.id)))).scalar_one() or 0)
        fast_sold = int((await session.execute(
            select(func.count(func.distinct(RadarLifecycleWatch.product_id))).where(
                RadarLifecycleWatch.status == "disappeared",
                RadarLifecycleWatch.lifetime_seconds.is_not(None),
                RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
            )
        )).scalar_one() or 0)
    return RadarStats(total, hot, rising, ai_picks, categories, signals, fast_sold)


async def list_radar_products(
    *, mode: str = "hot", category_key: str | None = None, page: int = 0,
    page_size: int = RADAR_PAGE_SIZE, user_id: int | None = None,
    price_filter: str = "any",
) -> tuple[list[RadarProduct], int]:
    """Return Radar products for the requested user-facing feed.

    v4.11.4 deliberately treats the category browser as the accumulated curated
    Radar catalogue.  No 24-hour filter is applied there: every product that was
    accepted into Radar remains visible.  The default category order is newest
    first, while ``category_best`` switches to DT Score ordering.
    """
    page = max(0, int(page))
    page_size = max(1, min(20, int(page_size)))
    async with SessionLocal() as session:
        query = select(RadarProduct)
        count_query = select(func.count(RadarProduct.id))
        conditions = []
        if category_key:
            conditions.append(RadarProduct.category_key == category_key)
        price_lo, price_hi = price_bounds(price_filter)
        if price_lo is not None or price_hi is not None:
            # A Radar row can represent a product family with several listings.
            # Filter by an actually observed listing price rather than by the
            # family's broad min/max envelope, otherwise a 50–500 € family could
            # incorrectly match every intermediate preset.
            price_conditions = [
                RadarProductListing.product_id == RadarProduct.id,
                RadarProductListing.last_price_eur.is_not(None),
            ]
            if price_lo is not None:
                price_conditions.append(RadarProductListing.last_price_eur >= int(price_lo))
            if price_hi is not None:
                price_conditions.append(RadarProductListing.last_price_eur <= int(price_hi))
            conditions.append(select(RadarProductListing.id).where(*price_conditions).exists())
        if mode == "hot":
            conditions.append(RadarProduct.status == "hot")
            order = (RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "rising":
            conditions.append(RadarProduct.status == "rising")
            order = (RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "ai":
            conditions.extend([
                RadarProduct.opportunity_type.in_(["hot_product", "hidden_gem", "emerging"]),
                RadarProduct.confidence >= 55,
            ])
            order = (RadarProduct.current_score.desc(), RadarProduct.confidence.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "fastsold":
            fast_product_ids = select(RadarLifecycleWatch.product_id).where(
                RadarLifecycleWatch.status == "disappeared",
                RadarLifecycleWatch.lifetime_seconds.is_not(None),
                RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
            )
            conditions.append(RadarProduct.id.in_(fast_product_ids))
            latest_disappearance = (
                select(func.max(RadarLifecycleWatch.disappeared_at))
                .where(
                    RadarLifecycleWatch.product_id == RadarProduct.id,
                    RadarLifecycleWatch.status == "disappeared",
                    RadarLifecycleWatch.lifetime_seconds.is_not(None),
                    RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
                )
                .correlate(RadarProduct).scalar_subquery()
            )
            fastest_lifetime = (
                select(func.min(RadarLifecycleWatch.lifetime_seconds))
                .where(
                    RadarLifecycleWatch.product_id == RadarProduct.id,
                    RadarLifecycleWatch.status == "disappeared",
                    RadarLifecycleWatch.lifetime_seconds.is_not(None),
                    RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
                )
                .correlate(RadarProduct).scalar_subquery()
            )
            order = (latest_disappearance.desc(), fastest_lifetime.asc(), RadarProduct.peak_score.desc())
        elif mode == "alltime":
            order = (RadarProduct.peak_score.desc(), RadarProduct.signal_count.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "favorites" and user_id is not None:
            fav_ids = select(RadarFavorite.product_id).where(RadarFavorite.user_id == int(user_id))
            conditions.append(RadarProduct.id.in_(fav_ids))
            order = (RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "category_best" and category_key:
            order = (RadarProduct.current_score.desc(), RadarProduct.first_radar_at.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "category_new" and category_key:
            order = (RadarProduct.first_radar_at.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        else:
            order = (RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        if conditions:
            query = query.where(*conditions)
            count_query = count_query.where(*conditions)
        total = int((await session.execute(count_query)).scalar_one() or 0)
        rows = list((await session.execute(
            query.order_by(*order).offset(page * page_size).limit(page_size)
        )).scalars().all())
        return rows, total


async def search_radar_products(
    query_text: str, *, page: int = 0, page_size: int = RADAR_PAGE_SIZE,
    price_filter: str = "any",
) -> tuple[list[RadarProduct], int]:
    """Simple mass-market Radar search by product title/model."""
    clean = " ".join(str(query_text or "").split()).strip()[:80]
    if len(clean) < 2:
        return [], 0
    page = max(0, int(page))
    page_size = max(1, min(20, int(page_size)))
    pattern = f"%{clean}%"
    async with SessionLocal() as session:
        conditions = [RadarProduct.title.ilike(pattern)]
        price_lo, price_hi = price_bounds(price_filter)
        if price_lo is not None or price_hi is not None:
            price_conditions = [
                RadarProductListing.product_id == RadarProduct.id,
                RadarProductListing.last_price_eur.is_not(None),
            ]
            if price_lo is not None:
                price_conditions.append(RadarProductListing.last_price_eur >= int(price_lo))
            if price_hi is not None:
                price_conditions.append(RadarProductListing.last_price_eur <= int(price_hi))
            conditions.append(select(RadarProductListing.id).where(*price_conditions).exists())
        total = int((await session.execute(
            select(func.count(RadarProduct.id)).where(*conditions)
        )).scalar_one() or 0)
        rows = list((await session.execute(
            select(RadarProduct)
            .where(*conditions)
            .order_by(RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
            .offset(page * page_size).limit(page_size)
        )).scalars().all())
    return rows, total


async def radar_categories() -> list[tuple[str, int, int, int]]:
    """Return accumulated category counts plus products newly added today.

    Tuple shape: ``(category_key, total_products, new_today, max_score)``.
    ``new_today`` uses the Moscow calendar day and ``first_radar_at`` so an old
    product receiving a fresh signal is not incorrectly presented as newly found.
    """
    moscow = ZoneInfo("Europe/Moscow")
    start_moscow = datetime.now(moscow).replace(hour=0, minute=0, second=0, microsecond=0)
    today_after_utc = start_moscow.astimezone(timezone.utc).replace(tzinfo=None)
    new_today_expr = func.sum(
        case((RadarProduct.first_radar_at >= today_after_utc, 1), else_=0)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(
                RadarProduct.category_key,
                func.count(RadarProduct.id),
                new_today_expr,
                func.max(RadarProduct.current_score),
            )
            .where(RadarProduct.category_key != "")
            .group_by(RadarProduct.category_key)
            .order_by(func.count(RadarProduct.id).desc(), func.max(RadarProduct.current_score).desc())
        )).all()
    return [
        (str(key), int(total or 0), int(new_today or 0), int(score or 0))
        for key, total, new_today, score in rows
    ]


async def get_radar_product(product_id: int) -> tuple[RadarProduct | None, Listing | None, list[RadarSnapshot]]:
    async with SessionLocal() as session:
        product = await session.get(RadarProduct, int(product_id))
        if product is None:
            return None, None, []
        listing = None
        if product.representative_external_id:
            listing = (await session.execute(select(Listing).where(
                Listing.external_id == product.representative_external_id
            ).limit(1))).scalar_one_or_none()
        snapshots = list((await session.execute(
            select(RadarSnapshot).where(RadarSnapshot.product_id == int(product_id))
            .order_by(RadarSnapshot.recorded_at.desc()).limit(8)
        )).scalars().all())
        return product, listing, snapshots


async def is_radar_favorite(user_id: int, product_id: int) -> bool:
    async with SessionLocal() as session:
        return bool((await session.execute(select(RadarFavorite.id).where(
            RadarFavorite.user_id == int(user_id), RadarFavorite.product_id == int(product_id)
        ).limit(1))).scalar_one_or_none())


async def toggle_radar_favorite(user_id: int, product_id: int) -> bool:
    async with _radar_lock:
        async with SessionLocal() as session:
            existing = (await session.execute(select(RadarFavorite).where(
                RadarFavorite.user_id == int(user_id), RadarFavorite.product_id == int(product_id)
            ).limit(1))).scalar_one_or_none()
            if existing is not None:
                await session.delete(existing)
                await session.commit()
                return False
            product = await session.get(RadarProduct, int(product_id))
            if product is None:
                return False
            session.add(RadarFavorite(user_id=int(user_id), product_id=int(product_id)))
            await session.commit()
            return True


async def backfill_radar_once() -> tuple[int, int]:
    """One-time migration of already saved scans + existing AI history into Radar."""
    async with SessionLocal() as session:
        setting = await session.get(AppSetting, RADAR_BACKFILL_SETTING)
        if setting is not None and str(setting.value or "").strip() == "1":
            return 0, 0
        candidate_ids = list((await session.execute(
            select(AIEarlyWinnerCandidate.id)
            .where(AIEarlyWinnerCandidate.is_control.is_(False))
            .order_by(AIEarlyWinnerCandidate.created_at.asc())
        )).scalars().all())
        scan_ids = list((await session.execute(
            select(UserScan.id).where(
                UserScan.status == "done", UserScan.target_complete.is_(True), UserScan.result_count > 0
            ).order_by(UserScan.finished_at.asc())
        )).scalars().all())

    ai_saved = 0
    for index, candidate_id in enumerate(candidate_ids, 1):
        try:
            if await record_ai_candidate(int(candidate_id), source_key=f"ai-backfill:{candidate_id}", source="ai_backfill") is not None:
                ai_saved += 1
        except Exception:
            log.exception("DT Radar AI backfill failed candidate=%s", candidate_id)
        if index % 100 == 0:
            await asyncio.sleep(0)

    scan_saved = 0
    for index, scan_id in enumerate(scan_ids, 1):
        try:
            scan_saved += await record_scan_hot(int(scan_id))
        except Exception:
            log.exception("DT Radar scan backfill failed scan=%s", scan_id)
        if index % 50 == 0:
            await asyncio.sleep(0)

    async with SessionLocal() as session:
        setting = await session.get(AppSetting, RADAR_BACKFILL_SETTING)
        if setting is None:
            session.add(AppSetting(key=RADAR_BACKFILL_SETTING, value="1", updated_at=datetime.utcnow()))
        else:
            setting.value = "1"
            setting.updated_at = datetime.utcnow()
        await session.commit()
    log.warning("DT Radar backfill complete | ai=%s scan_signals=%s", ai_saved, scan_saved)
    return ai_saved, scan_saved
