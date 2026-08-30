from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select, text, update

from db import DATABASE_BACKEND, SessionLocal
from early_winner import FeatureRow, listing_age_minutes, opportunity_family_key, score_initial_rows
from filters import price_bounds
from organic_velocity import (
    ORGANIC_HIGH_BASELINE_VIEWS, ORGANIC_HIGH_REQUIRED_CHECKPOINTS,
    demand_safe_metric, high_baseline_pending, is_high_baseline,
)
from parser import KleinanzeigenParser
from radar_ranking import (
    RADAR_48H_MAX_AGE_MINUTES, RadarRankEvidence, classify_radar_signal, demand_gate_for_age,
)
from traffic import TRAFFIC
from models import (
    AIEarlyWinnerCandidate,
    AIEarlyWinnerEvent,
    AIEarlyWinnerObservation,
    AIEarlyWinnerRun,
    AppSetting,
    Listing,
    ListingIntegrity,
    RadarFavorite,
    RadarLifecycleWatch,
    RadarProduct,
    RadarProductListing,
    RadarObservation,
    RadarSnapshot,
    ScanListing,
    UserScan,
)

log = logging.getLogger("dtparser-radar")

RADAR_BACKFILL_SETTING = "dt_radar_v1_backfill_complete"
RADAR_BUMP_SWEEP_SETTING = "dt_radar_v4156_bump_sweep_complete"
RADAR_BUMP_QUARANTINE_SETTING = "dt_radar_v4156_bump_quarantine_applied"
RADAR_VELOCITY_PREP_SETTING = "dt_radar_v4157_verified_velocity_prepared"
RADAR_UNIFIED_48H_REPAIR_SETTING = "dt_radar_v4200_unified_48h_repair_v2"
RADAR_V3_RESET_SETTING = "dt_radar_v3_observed_demand_reset_v4_radar31_audit_fix"
RADAR_V3_FIRST_CHECK_MINUTES = 60
RADAR_V3_NEXT_CHECK_MINUTES = 60
RADAR_V3_EARLY_CHECK_MINUTES = 45
RADAR_V3_STRONG_CHECK_MINUTES = 30
RADAR_V3_MAX_OBSERVATION_HOURS = 6
RADAR_V3_CANDIDATE_VPH = 15.0
RADAR_V3_SCORE_VPH = 30.0
RADAR_V3_STRONG_VPH = 60.0
RADAR_SCAN_TOP_LIMIT = 12
RADAR_PAGE_SIZE = 8
# v4.15.5: after parser-level HTTP + browser recovery, wait once and retry only
# the blocked detail candidate. This is much cheaper than re-scanning its category.
RADAR_DETAIL_FINAL_RETRY_SECONDS = 2.5

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
# v4.15.8: foreground Radar admission and background integrity/checkpoint work
# must never share a lock+parser pair. In v4.15.7 a background task could hold
# the old global lock, then wait for a background traffic lease after AutoScan
# became active, while AutoScan waited for that same lock: a true lock inversion.
_detail_gate_locks = {"normal": asyncio.Lock(), "background": asyncio.Lock(), "radar_checkpoint": asyncio.Lock()}
_detail_gate_parsers: dict[str, KleinanzeigenParser] = {}


async def _detail_gate_client(priority: str = "normal") -> KleinanzeigenParser:
    lane = str(priority) if str(priority) in {"background", "radar_checkpoint"} else "normal"
    parser = _detail_gate_parsers.get(lane)
    if parser is None:
        parser = KleinanzeigenParser()
        _detail_gate_parsers[lane] = parser
    return parser


def _visible_product_association_exists(product_id_expr):
    """Only strict-v4.15.4 certified families are user-visible.

    All current callers are RadarProduct queries, so the certification predicate
    must bind directly to the outer RadarProduct row (not an uncorrelated EXISTS).
    """
    return RadarProduct.organic_verified_at.is_not(None) & _clean_product_association_exists(product_id_expr)


async def _lock_integrity_external_id(session, external_id: str) -> None:
    """Serialize Radar admission against sticky integrity writes in PostgreSQL.

    v4.15.3 Strict Organic Radar Gate uses the same advisory-lock key as the
    parser-side integrity writer. Therefore either the organic Radar signal is
    committed first and a later contamination write purges it, or contamination
    commits first and the gate rejects the signal. There is no unguarded middle.
    """
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(CAST(hashtext(:integrity_key) AS bigint))"),
            {"integrity_key": f"organic-integrity:{str(external_id)}"},
        )


def _registry_dirty_exists(external_id_expr):
    return select(ListingIntegrity.external_id).where(
        ListingIntegrity.external_id == external_id_expr,
        (ListingIntegrity.is_promoted.is_(True)) | (ListingIntegrity.is_price_reduced.is_(True)),
    ).exists()


def _clean_listing_exists(external_id_expr):
    return select(Listing.external_id).where(
        Listing.external_id == external_id_expr,
        Listing.is_promoted.is_(False),
        Listing.is_price_reduced.is_(False),
        ~_registry_dirty_exists(Listing.external_id),
    ).exists()


def _clean_product_association_exists(product_id_expr):
    """Strict product visibility: at least one association and every one is clean.

    Hiding the whole family for the tiny interval before purge/rebuild is safer than
    exposing an aggregate score that could still include one newly contaminated
    listing. After cleanup removes that association the clean family reappears.
    """
    has_clean = (
        select(RadarProductListing.id)
        .where(
            RadarProductListing.product_id == product_id_expr,
            _clean_listing_exists(RadarProductListing.external_id),
        )
        .exists()
    )
    has_unverified_or_dirty = (
        select(RadarProductListing.id)
        .where(
            RadarProductListing.product_id == product_id_expr,
            ~_clean_listing_exists(RadarProductListing.external_id),
        )
        .exists()
    )
    return has_clean & ~has_unverified_or_dirty


async def _strict_organic_gate(session, external_id: str) -> tuple[bool, str]:
    """DB-authoritative admission check for every new Radar signal.

    The passed ORM object is deliberately not trusted. Main Bot, AI Worker and
    Lifecycle Worker are separate Railway processes and may hold stale objects.
    Radar admission is allowed only when the current Listing row is explicitly
    clean *and* the sticky listing_integrity registry has no contamination flag.
    """
    external_id = str(external_id or "").strip()
    if not external_id:
        return False, "missing_external_id"
    await _lock_integrity_external_id(session, external_id)
    listing_state = (await session.execute(
        select(Listing.is_promoted, Listing.is_price_reduced)
        .where(Listing.external_id == external_id)
        .limit(1)
    )).one_or_none()
    if listing_state is None:
        return False, "listing_missing"
    is_promoted, is_price_reduced = listing_state
    if bool(is_promoted):
        return False, "listing_promoted"
    if bool(is_price_reduced):
        return False, "listing_price_reduced"
    registry_dirty = bool((await session.execute(
        select(ListingIntegrity.external_id).where(
            ListingIntegrity.external_id == external_id,
            (ListingIntegrity.is_promoted.is_(True)) | (ListingIntegrity.is_price_reduced.is_(True)),
        ).limit(1)
    )).scalar_one_or_none())
    if registry_dirty:
        return False, "sticky_registry"
    return True, "organic"


def _safe_iso_day(value: str | None):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _listing_resurrection_reason(listing: Listing) -> str:
    """Detect impossible chronology for the same external_id without guessing from views."""
    current_day = _safe_iso_day(getattr(listing, "posted_date_msk", None))
    original_day = _safe_iso_day(getattr(listing, "first_posted_date_msk", None))
    if current_day is None:
        return ""
    if original_day is not None and current_day > original_day:
        return "resurfaced_posted_date_shift"
    first_seen = getattr(listing, "first_seen_at", None)
    if first_seen is not None:
        aware = first_seen.replace(tzinfo=timezone.utc) if first_seen.tzinfo is None else first_seen.astimezone(timezone.utc)
        first_seen_day = aware.astimezone(ZoneInfo("Europe/Moscow")).date()
        if current_day > first_seen_day:
            return "resurfaced_after_first_seen"
    return ""


async def _mark_detail_nonorganic(
    external_id: str, *, promoted: bool, reduced: bool, promotion_reason: str = ""
) -> None:
    """Persist a live detail-page dirty verdict, then purge only that ad's analytics."""
    external_id = str(external_id or "").strip()
    if not external_id or not (promoted or reduced):
        return
    now = datetime.utcnow()
    async with SessionLocal() as session:
        await _lock_integrity_external_id(session, external_id)
        listing = (await session.execute(
            select(Listing).where(Listing.external_id == external_id).limit(1)
        )).scalar_one_or_none()
        if listing is not None:
            listing.is_promoted = bool(listing.is_promoted or promoted)
            listing.is_price_reduced = bool(listing.is_price_reduced or reduced)
        entry = await session.get(ListingIntegrity, external_id)
        if entry is None:
            entry = ListingIntegrity(
                external_id=external_id,
                is_promoted=bool(promoted),
                is_price_reduced=bool(reduced),
                first_detected_at=now,
                last_detected_at=now,
                promotion_reason=(str(promotion_reason)[:80] if promoted and promotion_reason else ""),
            )
            session.add(entry)
        else:
            entry.is_promoted = bool(entry.is_promoted or promoted)
            entry.is_price_reduced = bool(entry.is_price_reduced or reduced)
            if promoted and promotion_reason:
                entry.promotion_reason = str(promotion_reason)[:80]
            entry.last_detected_at = now
        await session.commit()
    # Targeted idempotent cleanup keeps AI/Radar/Lifecycle history consistent.
    await purge_nonorganic_analytics(
        external_ids=[external_id], infer_historical_price_drops=False
    )


async def _live_detail_organic_gate(
    listing: Listing, *, force_priority: str | None = None
) -> tuple[bool, str, datetime | None]:
    """Final public detail-page gate. Unknown is never promoted to organic.

    Maintenance sweeps may force background priority so they never steal traffic
    from a foreground user/AutoScan job. Normal Radar admission keeps the existing
    v4.15.5 priority behavior.
    """
    external_id = str(getattr(listing, "external_id", "") or "").strip()
    url = str(getattr(listing, "url", "") or "").strip()
    if not external_id or not url:
        return False, "missing_detail_identity", None
    resurrection_reason = _listing_resurrection_reason(listing)
    if resurrection_reason:
        await _mark_detail_nonorganic(
            external_id, promoted=True, reduced=False, promotion_reason=resurrection_reason
        )
        return False, f"detail_promoted:{resurrection_reason}", None
    # Foreground Radar admission (user scan / AutoScan) must never infer
    # background priority from the traffic policy. During an AutoScan round the
    # background lane is intentionally paused; inferring ``background`` here
    # would make the foreground category wait on its own pause. Maintenance
    # callers that are genuinely low-priority pass force_priority="background"
    # explicitly (sweep / Verified Organic Velocity).
    detail_priority = (
        str(force_priority)
        if force_priority in {"normal", "background", "radar_checkpoint"}
        else "normal"
    )
    detail_lane = detail_priority if detail_priority in {"background", "radar_checkpoint"} else "normal"
    async with _detail_gate_locks[detail_lane]:
        parser = await _detail_gate_client(detail_lane)
        verdict = await parser.inspect_detail_integrity(
            url, expected_external_id=external_id, traffic_priority=detail_priority
        )
        if not verdict.verified and str(verdict.reason or "") != "unavailable":
            # v4.15.5 targeted recovery: retry only this exact candidate after the
            # parser exhausted HTTP + rendered-browser recovery. Do not re-scan 15
            # category pages merely because one detail request was transiently weak.
            if RADAR_DETAIL_FINAL_RETRY_SECONDS > 0:
                await asyncio.sleep(RADAR_DETAIL_FINAL_RETRY_SECONDS)
            try:
                await parser.reset_scan_browser_context()
            except Exception:
                log.debug("Final detail retry context reset failed external_id=%s", external_id, exc_info=True)
            verdict = await parser.inspect_detail_integrity(
                url, expected_external_id=external_id, traffic_priority=detail_priority
            )
    if not verdict.verified:
        return False, str(verdict.reason or "detail_unknown"), None
    if verdict.is_promoted or verdict.is_price_reduced:
        await _mark_detail_nonorganic(
            external_id, promoted=bool(verdict.is_promoted), reduced=bool(verdict.is_price_reduced),
            promotion_reason=str(getattr(verdict, "promotion_reason", "") or "detail_promoted"),
        )
        if verdict.is_promoted and verdict.is_price_reduced:
            reason = "detail_promoted_and_reduced"
        elif verdict.is_promoted:
            reason = "detail_promoted"
        else:
            reason = "detail_price_reduced"
        return False, reason, None
    return True, "organic", datetime.utcnow()


async def verify_listing_organic_now(
    external_id: str, *, traffic_priority: str = "background"
) -> tuple[bool, str, datetime | None]:
    """Public wrapper used by v4.15.7 checkpoint verification."""
    external_id = str(external_id or "").strip()
    if not external_id:
        return False, "missing_external_id", None
    async with SessionLocal() as session:
        listing = (await session.execute(
            select(Listing).where(
                Listing.external_id == external_id,
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            ).limit(1)
        )).scalar_one_or_none()
        if listing is not None:
            session.expunge(listing)
    if listing is None:
        return False, "listing_not_clean", None
    return await _live_detail_organic_gate(listing, force_priority=traffic_priority)


@dataclass(frozen=True)
class RadarAdmissionStats:
    eligible_with_views: int = 0
    high_baseline_pending: int = 0
    high_baseline_verified: int = 0
    reserve_considered: int = 0
    detail_checked: int = 0
    organic_passed: int = 0
    promoted_blocked: int = 0
    reduced_blocked: int = 0
    unknown_blocked: int = 0
    unknown_reasons: tuple[tuple[str, int], ...] = ()
    db_blocked: int = 0
    demand_gate_rejected: int = 0
    qualified_candidates: int = 0
    early_admitted: int = 0
    strong_admitted: int = 0
    hot_admitted: int = 0
    admitted: int = 0
    already_present: int = 0
    saved: int = 0


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


def _organic_view_metric(
    listing: Listing, raw_views: int | None, measured_at: datetime | None = None
) -> tuple[int | None, str]:
    """Return the v4.15.7 demand-safe view quantity for ranking/scoring."""
    metric = demand_safe_metric(listing, raw_views, measured_at)
    return metric.views, metric.kind


def _feature_for_listing(
    listing: Listing, *, raw_views: int | None = None, measured_at: datetime | None = None
) -> tuple[FeatureRow, str] | None:
    """Build one demand-safe 48H feature or reject evidence with an unsafe clock."""
    when = measured_at or listing.views_checked_at or listing.last_seen_at or datetime.utcnow()
    raw = listing.view_count if raw_views is None else raw_views
    metric = demand_safe_metric(listing, raw, when)
    if metric.views is None:
        return None

    # v4.20.0 Unified 48H uses two different clocks by design:
    #   * listing age -> age cohort + Demand Gate;
    #   * DT observation window -> verified-delta views/hour.
    # Never let a yesterday listing become a 0-3h listing merely because DT first
    # established its organic baseline one hour ago.
    age_minutes, exact_clock = listing_age_minutes(listing.posted_text, when)
    if not exact_clock or age_minutes is None:
        return None
    age = float(age_minutes)
    if age < 5.0 or age > RADAR_48H_MAX_AGE_MINUTES:
        return None
    velocity_window_minutes = (
        float(metric.age_minutes)
        if metric.kind == "observed_delta" and metric.age_minutes is not None
        else age
    )
    if velocity_window_minutes <= 0.0:
        return None
    category_key = str(listing.category_key or "unknown")
    return FeatureRow(
        external_id=str(listing.external_id),
        category_key=category_key,
        identity_key=listing.identity_key,
        identity_label=listing.identity_label,
        identity_confidence=listing.identity_confidence,
        price_eur=listing.price_eur,
        views=int(metric.views),
        age_minutes=age,
        title=str(listing.title or ""),
        family_key=opportunity_family_key(str(listing.title or ""), category_key),
        velocity_window_minutes=velocity_window_minutes,
    ), str(metric.kind)


def _simple_48h_market_stats(features: list[FeatureRow]) -> dict[str, dict]:
    """Use the 48H cohort itself for price-fit evidence without inventing history."""
    prices: dict[str, list[int]] = {}
    counts: Counter[str] = Counter()
    for row in features:
        key = (
            f"id:{row.identity_key}"
            if row.identity_key and int(row.identity_confidence or 0) >= 70
            else (row.family_key or opportunity_family_key(row.title, row.category_key))
        )
        if not key:
            continue
        counts[key] += 1
        if row.price_eur is not None and int(row.price_eur) > 0:
            prices.setdefault(key, []).append(int(row.price_eur))
    result: dict[str, dict] = {}
    for key, count in counts.items():
        values = sorted(prices.get(key, []))
        median = None
        if values:
            mid = len(values) // 2
            median = float(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0)
        result[key] = {"median": median, "count": int(count)}
    return result


async def _score_unified_48h_category(
    category_key: str,
    candidate_ids: set[str] | list[str] | tuple[str, ...],
    *,
    overrides: dict[str, tuple[int | None, datetime | None]] | None = None,
) -> tuple[dict[str, tuple[Listing, FeatureRow, object, str]], int]:
    """Score candidates against all demand-safe listings in the same 48H category.

    This is the core of the unified today+yesterday Radar.  A 2-hour listing and a
    30-hour listing are allowed into the same public TOP, while ``score_initial_rows``
    compares Relative Velocity inside its explicit age bands.
    """
    ids = {str(x).strip() for x in candidate_ids if str(x).strip()}
    if not ids:
        return {}, 0
    now = datetime.utcnow()
    async with SessionLocal() as session:
        peers = list((await session.execute(
            select(Listing).where(
                Listing.category_key == str(category_key),
                Listing.last_seen_at >= now - timedelta(hours=52),
                Listing.is_active.is_(True),
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                Listing.view_count.is_not(None),
                ~_registry_dirty_exists(Listing.external_id),
            ).order_by(Listing.last_seen_at.desc()).limit(5000)
        )).scalars().all())
    overrides = overrides or {}
    features: list[FeatureRow] = []
    kind_by_id: dict[str, str] = {}
    listing_by_id: dict[str, Listing] = {}
    for listing in peers:
        ext = str(listing.external_id)
        raw, measured = overrides.get(ext, (listing.view_count, listing.views_checked_at or listing.last_seen_at))
        built = _feature_for_listing(listing, raw_views=raw, measured_at=measured)
        if built is None:
            continue
        feature, metric_kind = built
        features.append(feature)
        kind_by_id[ext] = metric_kind
        listing_by_id[ext] = listing
    if not features:
        return {}, 0
    market_stats = _simple_48h_market_stats(features)
    score_map = {score.external_id: score for score in score_initial_rows(features, market_stats)}
    feature_map = {feature.external_id: feature for feature in features}
    result: dict[str, tuple[Listing, FeatureRow, object, str]] = {}
    for ext in ids:
        listing = listing_by_id.get(ext)
        feature = feature_map.get(ext)
        score = score_map.get(ext)
        if listing is not None and feature is not None and score is not None:
            result[ext] = (listing, feature, score, kind_by_id.get(ext, "unknown"))
    return result, len(features)


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


def _effective_score(product: RadarProduct, now: datetime) -> int:
    """Return the real DT Demand Score of the live representative signal.

    Unified 48H ranking must never manufacture extra DT Score points from signal
    count, confirmation count or time decay. Repeatability/Persistence already live
    inside the fixed 40/20/15/15/10 model; Radar Rank is the separate ordering layer.
    """
    return _clamp_score(int(product.last_signal_score or 0))


def _snapshot_live_evidence(snapshot: RadarSnapshot, now: datetime):
    """Re-evaluate one persisted signal at *current* age without inventing new views.

    A listing that had just enough demand to be Hot at 3h must not stay Hot forever
    if no new views arrive. We conservatively advance the evidence clock while
    keeping demand_views frozen at the last exact measurement, so Demand Gate can
    downgrade stale signals until a fresh observation proves continued growth.
    """
    recorded_at = snapshot.recorded_at
    elapsed_minutes = 0.0
    if recorded_at is not None:
        elapsed_minutes = max(0.0, (now - recorded_at).total_seconds() / 60.0)
    effective_age = max(0.0, float(getattr(snapshot, "demand_age_minutes", 0.0) or 0.0)) + elapsed_minutes
    if str(getattr(snapshot, "source", "") or "") == "radar3_observed":
        # Radar 3.1 evidence is live, not a 48-hour trophy. Once the DT-owned
        # observation window is stale, an old Hot/Strong snapshot must not
        # resurrect a product when a later weaker checkpoint arrives.
        if elapsed_minutes > float(RADAR_V3_MAX_OBSERVATION_HOURS * 60):
            return RadarRankEvidence("historical", 0.0, 0, 1.0, 0.0, False)
        status = str(getattr(snapshot, "demand_status", "stable") or "stable")
        return RadarRankEvidence(status, float(getattr(snapshot, "radar_rank", 0.0) or 0.0), 0, 1.0, 0.0, True)
    return classify_radar_signal(
        dt_score=int(snapshot.score or 0),
        confidence=int(snapshot.confidence or 0),
        demand_views=int(getattr(snapshot, "demand_views", 0) or 0),
        age_minutes=effective_age,
    )


def _next_lifecycle_checkpoint(first_seen_at: datetime, now: datetime) -> tuple[int, datetime] | None:
    elapsed = max(0.0, (now - first_seen_at).total_seconds() / 60.0)
    for step, minutes in enumerate(RADAR_LIFECYCLE_CHECK_MINUTES):
        if elapsed < float(minutes):
            return step, first_seen_at + timedelta(minutes=int(minutes))
    return None


async def _maybe_queue_lifecycle_watch(
    session, *, product: RadarProduct, listing: Listing, score: int, now: datetime,
    demand_status: str = "historical",
) -> None:
    """Enroll a fresh strong listing in the durable Lifecycle queue.

    The helper runs inside the same transaction as the Radar signal. Existing
    watches are only refreshed; disappeared/expired history is never resurrected.
    """
    if int(score or 0) < RADAR_LIFECYCLE_MIN_SCORE:
        return
    if str(demand_status or "") not in {"hot", "rising"}:
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
    live_detail_verified_at: datetime | None = None,
    demand_views: int | None = None,
    demand_age_minutes: float | None = None,
    radar_rank: float | None = None,
    demand_status: str | None = None,
    demand_gate: int | None = None,
) -> int | None:
    """Append one idempotent Radar snapshot and refresh its aggregate product."""
    # v4.15.2: Radar is organic-demand only. Keep this defensive guard even
    # though parser/AI sources filter earlier, so no future ingestion path can
    # accidentally reintroduce paid TOP/bumped or price-reduced listings.
    if bool(getattr(listing, "is_promoted", False)) or bool(getattr(listing, "is_price_reduced", False)):
        return None
    if live_detail_verified_at is None:
        detail_allowed, detail_reason, live_detail_verified_at = await _live_detail_organic_gate(listing)
        if not detail_allowed:
            log.warning(
                "Strict Organic live-detail blocked source=%s external_id=%s reason=%s",
                source, listing.external_id, detail_reason,
            )
            return None
    now = recorded_at or datetime.utcnow()
    score = _clamp_score(score)
    confidence = _clamp_score(confidence)
    if demand_views is None or demand_age_minutes is None:
        built = _feature_for_listing(
            listing, raw_views=(view_count if view_count is not None else listing.view_count), measured_at=now
        )
        if built is not None:
            fallback_feature, _fallback_kind = built
            if demand_views is None:
                demand_views = int(fallback_feature.views)
            if demand_age_minutes is None:
                demand_age_minutes = float(fallback_feature.age_minutes)
    if str(source) == "radar3_observed":
        # Radar 3.0 is governed by DT-owned observation checkpoints, not listing age
        # or inherited counters. The caller status is evidence-stage based.
        demand_status = str(demand_status or "stable")
        radar_rank = float(radar_rank if radar_rank is not None else score)
        demand_gate = 0
        evidence = RadarRankEvidence(demand_status, radar_rank, 0, 1.0, 0.0, True)
    else:
        evidence = classify_radar_signal(
            dt_score=score, confidence=confidence, demand_views=demand_views, age_minutes=demand_age_minutes
        )
        # Legacy admission paths retain their historical guard, but Radar 3.0
        # publishers are the only active paths in this release.
        radar_rank = float(evidence.radar_rank)
        demand_status = str(evidence.status)
        demand_gate = int(evidence.demand_gate if evidence.demand_gate < 10**9 else 0)
    demand_views = max(0, int(demand_views or 0))
    demand_age_minutes = max(0.0, float(demand_age_minutes or 0.0))
    radar_rank = max(0.0, min(100.0, radar_rank))
    demand_status = demand_status[:24]
    demand_gate = max(0, demand_gate)
    reason_list = [str(x) for x in (reasons or []) if str(x).strip()]
    latest_reason = (reason_list[0] if reason_list else "")[:800]

    async with _radar_lock:
        async with SessionLocal() as session:
            # v4.15.3 Strict Organic Radar Gate: re-check the authoritative DB
            # state inside the Radar transaction. A detached/stale Listing object
            # is not enough to admit a signal. The shared integrity advisory lock
            # closes the cross-process race with parser-side sticky flag writes.
            allowed, gate_reason = await _strict_organic_gate(session, str(listing.external_id or ""))
            if not allowed:
                log.warning(
                    "Strict Organic Radar Gate blocked source=%s external_id=%s reason=%s",
                    source, listing.external_id, gate_reason,
                )
                return None

            # Main Bot and AI Worker are separate Railway processes. Serialize only
            # writes for the same product family so both can safely discover a new
            # Radar product at the same time without a unique-key race.
            bind = session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(CAST(hashtext(:radar_key) AS bigint))"),
                    {"radar_key": product_key},
                )
            duplicate_product_id = (await session.execute(
                select(RadarSnapshot.product_id).where(RadarSnapshot.source_key == source_key).limit(1)
            )).scalar_one_or_none()
            if duplicate_product_id is not None:
                # Idempotent retries are success, not a new signal. This matters for
                # AutoScan review/retry rounds: a higher-ranked candidate already
                # committed before a later UNKNOWN gate must not inflate repeatability.
                existing_product = await session.get(RadarProduct, int(duplicate_product_id))
                if existing_product is not None and existing_product.organic_verified_at is not None:
                    return int(existing_product.id)
                # A legacy pre-v4.15.4 duplicate remains quarantined. Continue: the
                # legacy reset below deletes old snapshots before writing strict data.

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
                    organic_verified_at=live_detail_verified_at or datetime.utcnow(),
                    bump_sweep_verified_at=live_detail_verified_at or datetime.utcnow(),
                    last_signal_at=now,
                    last_signal_score=score,
                    current_score=score,
                    peak_score=score,
                    confidence=confidence,
                    radar_rank=radar_rank,
                    demand_views=demand_views,
                    demand_age_minutes=demand_age_minutes,
                    demand_gate=demand_gate,
                    status=demand_status,
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
            elif product.organic_verified_at is None:
                # First strict-v4.15.4 certification of a legacy family: discard
                # pre-gate aggregate evidence but keep the stable product id/favorites.
                await session.execute(delete(RadarSnapshot).where(RadarSnapshot.product_id == int(product.id)))
                await session.execute(delete(RadarProductListing).where(RadarProductListing.product_id == int(product.id)))
                await session.execute(delete(RadarLifecycleWatch).where(RadarLifecycleWatch.product_id == int(product.id)))
                product.first_radar_at = now
                product.signal_count = 0
                product.confirmed_count = 0
                product.listing_count = 0
                product.best_views = 0
                product.best_views_per_hour = 0.0
                product.min_price_eur = None
                product.max_price_eur = None
                product.current_score = score
                product.peak_score = score
                product.radar_rank = radar_rank
                product.demand_views = demand_views
                product.demand_age_minutes = demand_age_minutes
                product.demand_gate = demand_gate
                product.status = demand_status
                product.latest_reason = ""
                product.latest_source = ""
                product.last_ai_candidate_id = None
            product.organic_verified_at = live_detail_verified_at or datetime.utcnow()
            product.bump_sweep_verified_at = live_detail_verified_at or datetime.utcnow()

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
                radar_rank=radar_rank,
                demand_views=demand_views,
                demand_age_minutes=demand_age_minutes,
                demand_gate=demand_gate,
                demand_status=demand_status,
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
                product.radar_rank = max(float(product.radar_rank or 0.0), radar_rank)
                product.demand_views = max(int(product.demand_views or 0), demand_views)
                product.demand_age_minutes = max(float(product.demand_age_minutes or 0.0), demand_age_minutes)
                product.demand_gate = max(int(product.demand_gate or 0), demand_gate)
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
                .where(
                    RadarSnapshot.product_id == int(product.id),
                    RadarSnapshot.recorded_at >= now - timedelta(hours=48),
                )
                .order_by(RadarSnapshot.recorded_at.desc(), RadarSnapshot.id.desc())
                .limit(300)
            )).scalars().all())
            latest_by_listing: dict[str, RadarSnapshot] = {}
            for snap in recent_snapshots:
                ext = str(snap.external_id or f"snapshot:{snap.id}")
                if ext not in latest_by_listing:
                    latest_by_listing[ext] = snap
            live_ranked = []
            for snap in latest_by_listing.values():
                live_evidence = _snapshot_live_evidence(snap, now)
                if live_evidence.admitted:
                    live_ranked.append((snap, live_evidence))
            if live_ranked:
                strongest_ranked, strongest_evidence = max(
                    live_ranked,
                    key=lambda pair: (float(pair[1].radar_rank), int(pair[0].score or 0), int(pair[0].confidence or 0)),
                )
                product.last_signal_score = int(strongest_ranked.score or 0)
                product.last_signal_at = strongest_ranked.recorded_at
                product.radar_rank = float(strongest_evidence.radar_rank)
                product.demand_views = int(getattr(strongest_ranked, "demand_views", 0) or 0)
                product.demand_age_minutes = float(getattr(strongest_ranked, "demand_age_minutes", 0.0) or 0.0)
                product.demand_gate = int(strongest_evidence.demand_gate if strongest_evidence.demand_gate < 10**9 else 0)
                product.status = str(strongest_evidence.status)
                product.confidence = int(strongest_ranked.confidence or 0)
                product.opportunity_type = str(strongest_ranked.opportunity_type or "spark")[:32]
                product.representative_external_id = str(strongest_ranked.external_id or listing.external_id)
                product.latest_source = str(strongest_ranked.source or "")[:32]
                product.last_ai_candidate_id = strongest_ranked.candidate_id
                try:
                    strongest_reasons = json.loads(strongest_ranked.reasons_json or "[]")
                    product.latest_reason = str(strongest_reasons[0] if isinstance(strongest_reasons, list) and strongest_reasons else "")[:800]
                except Exception:
                    product.latest_reason = ""
                product.current_score = _effective_score(product, now)
            else:
                product.last_signal_score = 0
                product.current_score = 0
                product.radar_rank = 0.0
                product.demand_views = 0
                product.demand_age_minutes = 0.0
                product.demand_gate = 0
                product.status = "historical"
            product.updated_at = now
            await _maybe_queue_lifecycle_watch(
                session, product=product, listing=listing, score=score, now=now, demand_status=demand_status
            )
            await session.commit()
            return int(product.id)


async def record_scan_hot(scan_id: int, limit: int = RADAR_SCAN_TOP_LIMIT) -> int:
    """Merge unified 48H demand-scored listings from a completed user scan.

    v4.20.0 no longer manufactures a Hot score from TOP position. Every candidate
    receives the real evidence-adaptive DT Demand Score, then must pass the age-aware
    absolute Demand Gate before it can be Strong/Hot.
    """
    # Radar 3.0: legacy admission path disabled; only DT-observed checkpoints may publish.
    return 0
    async with SessionLocal() as session:
        scan = await session.get(UserScan, int(scan_id))
        if scan is None or scan.status != "done" or not scan.target_complete:
            return 0
        pairs = (await session.execute(
            select(Listing, ScanListing)
            .join(ScanListing, Listing.external_id == ScanListing.external_id)
            .where(
                ScanListing.scan_id == int(scan_id),
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            )
        )).all()
    by_category: dict[str, list[tuple[Listing, ScanListing]]] = {}
    for listing, snap in pairs:
        if snap.initial_view_count is None:
            continue
        by_category.setdefault(str(listing.category_key or "unknown"), []).append((listing, snap))

    ranked: list[tuple[float, Listing, ScanListing, FeatureRow, object, str, object]] = []
    for category_key, category_pairs in by_category.items():
        ids = {str(listing.external_id) for listing, _snap in category_pairs}
        overrides = {
            str(listing.external_id): (int(snap.initial_view_count), snap.captured_at)
            for listing, snap in category_pairs if snap.initial_view_count is not None
        }
        scored, _cohort_size = await _score_unified_48h_category(
            category_key, ids, overrides=overrides
        )
        snap_by_id = {str(listing.external_id): snap for listing, snap in category_pairs}
        for ext, (listing, feature, score, metric_kind) in scored.items():
            evidence = classify_radar_signal(
                dt_score=int(score.score or 0), confidence=int(score.confidence or 0),
                demand_views=int(feature.views), age_minutes=float(feature.age_minutes),
            )
            if not evidence.admitted:
                continue
            snap = snap_by_id.get(ext)
            if snap is None:
                continue
            ranked.append((float(evidence.radar_rank), listing, snap, feature, score, metric_kind, evidence))

    ranked.sort(key=lambda item: (item[0], int(item[4].score or 0), int(item[3].views)), reverse=True)
    target = max(1, int(limit))
    saved = 0
    for _rank, listing, snap, feature, score, metric_kind, evidence in ranked:
        if saved >= target:
            break
        allowed, detail_reason, verified_at = await _live_detail_organic_gate(listing)
        if not allowed:
            log.info(
                "Radar scan candidate rejected scan=%s external_id=%s reason=%s",
                scan_id, listing.external_id, detail_reason,
            )
            if "promoted" not in detail_reason and "reduced" not in detail_reason:
                break
            continue
        result = await _upsert_signal(
            source_key=f"scan-hot:{scan_id}:{listing.external_id}",
            source="scan_hot",
            listing=listing,
            product_key=radar_product_key(listing, score.cohort_key),
            score=int(score.score),
            confidence=int(score.confidence or 0),
            stage=str(score.stage or "watch"),
            opportunity_type=str(score.opportunity_type or "spark"),
            scan_id=int(scan_id),
            view_count=int(snap.initial_view_count or 0),
            views_per_hour=float(score.views_per_hour or 0.0),
            reasons=[
                f"Unified 48H: {evidence.status} · DT Score {int(score.score)}/100 · Radar Rank {float(evidence.radar_rank):.1f}",
                f"Demand Gate: {int(feature.views)}/{int(evidence.demand_gate)} demand-safe views · age {float(feature.age_minutes)/60.0:.1f}h",
                (f"DT-observed delta: {int(feature.views)}" if metric_kind == "observed_delta" else "Fresh total verified after Organic Gate"),
                *[str(x) for x in tuple(score.reasons or ())[:2]],
            ],
            recorded_at=snap.captured_at,
            live_detail_verified_at=verified_at,
            demand_views=int(feature.views),
            demand_age_minutes=float(feature.age_minutes),
            radar_rank=float(evidence.radar_rank),
            demand_status=str(evidence.status),
            demand_gate=int(evidence.demand_gate),
        )
        if result is not None:
            saved += 1
    if saved:
        log.info("DT Radar unified scan merge scan=%s products=%s candidates=%s", scan_id, saved, len(ranked))
    return saved


async def record_autoscan_hot_detailed(
    round_id: str,
    category_key: str,
    matched_ids: list[str] | tuple[str, ...] | set[str],
    *,
    limit: int = RADAR_SCAN_TOP_LIMIT,
    emit_signals: bool = True,
) -> RadarAdmissionStats:
    """Radar 3.0 baseline pass.

    A first counter never votes. AutoScan only creates DT-owned baselines here;
    all user-visible Radar signals are created later from measured post-baseline
    growth. This makes hidden bumps with inherited counters harmless.
    """
    ids = list(dict.fromkeys(str(x).strip() for x in matched_ids if str(x).strip()))[:5000]
    if not ids:
        return RadarAdmissionStats()
    now = datetime.utcnow()
    seeded = 0
    async with SessionLocal() as session:
        rows = list((await session.execute(select(Listing).where(
            Listing.external_id.in_(ids),
            Listing.category_key == str(category_key),
            Listing.is_promoted.is_(False),
            Listing.is_price_reduced.is_(False),
            Listing.view_count.is_not(None),
            ~_registry_dirty_exists(Listing.external_id),
        ))).scalars().all())
        existing_rows = list((await session.execute(
            select(RadarObservation).where(RadarObservation.external_id.in_(ids))
        )).scalars().all())
        existing_map = {str(x.external_id): x for x in existing_rows}
        for listing in rows:
            ext = str(listing.external_id)
            measured_at = listing.views_checked_at or listing.last_seen_at or now
            raw = max(0, int(listing.view_count or 0))
            existing = existing_map.get(ext)
            if existing is not None:
                # A completed/quiet observation may be re-armed by a later Radar
                # circle, but an active observation is never rebased mid-flight.
                old_enough = (now - (existing.updated_at or existing.last_measured_at or now)).total_seconds() >= 3 * 3600
                if str(existing.status or "") in {"quiet", "expired"} and old_enough:
                    existing.baseline_views = raw
                    existing.baseline_at = measured_at
                    existing.last_views = raw
                    existing.last_measured_at = measured_at
                    existing.checkpoint_count = 0
                    existing.positive_checkpoints = 0
                    existing.consecutive_positive = 0
                    existing.total_delta = 0
                    existing.current_vph = 0.0
                    existing.previous_vph = 0.0
                    existing.peak_vph = 0.0
                    existing.velocity_percentile = 0.0
                    existing.acceleration_ratio = 0.0
                    existing.confidence = 0
                    existing.scored_checkpoints = 0
                    existing.consecutive_scored = 0
                    existing.strong_checkpoints = 0
                    existing.consecutive_strong = 0
                    existing.lease_owner = ""
                    existing.lease_until = None
                    existing.status = "baseline"
                    existing.next_check_at = measured_at + timedelta(minutes=RADAR_V3_FIRST_CHECK_MINUTES)
                    existing.expires_at = measured_at + timedelta(hours=RADAR_V3_MAX_OBSERVATION_HOURS)
                    existing.updated_at = now
                    seeded += 1
                continue
            session.add(RadarObservation(
                external_id=ext, category_key=str(category_key),
                product_key=radar_product_key(listing),
                baseline_views=raw, baseline_at=measured_at,
                last_views=raw, last_measured_at=measured_at,
                checkpoint_count=0, positive_checkpoints=0, consecutive_positive=0,
                total_delta=0, current_vph=0.0, peak_vph=0.0, status="baseline",
                next_check_at=measured_at + timedelta(minutes=RADAR_V3_FIRST_CHECK_MINUTES),
                expires_at=measured_at + timedelta(hours=RADAR_V3_MAX_OBSERVATION_HOURS),
                created_at=now, updated_at=now,
            ))
            seeded += 1
        if seeded:
            await session.commit()
    log.info("DT Radar 3.0 baselines round=%s category=%s rows=%s seeded=%s first_counter_votes=0", round_id, category_key, len(rows), seeded)
    return RadarAdmissionStats(eligible_with_views=len(rows), admitted=0, saved=0)


async def record_user_scan_radar3_baselines(scan_id: int) -> int:
    """Seed Radar 3.0 from a completed *today* user scan without extra web requests.

    ScanListing.initial_view_count/captured_at are authoritative baseline points.
    User scans never publish a signal by themselves and duplicate adIds reuse the
    existing RadarObservation instead of creating another observation.
    """
    now = datetime.utcnow()
    today_msk = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    seeded = 0
    async with SessionLocal() as session:
        # Multiple users can finish scans containing the same adId at nearly the
        # same moment. Serialize this very short DB-only seeding section so the
        # unique RadarObservation.external_id constraint never becomes a race.
        if DATABASE_BACKEND == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "radar3-user-scan-baseline-seed"},
            )
        scan = await session.get(UserScan, int(scan_id))
        if scan is None or str(scan.target_date or "") != today_msk or str(scan.status or "") not in {"done", "partial"}:
            return 0
        rows = list((await session.execute(
            select(ScanListing, Listing)
            .join(Listing, Listing.external_id == ScanListing.external_id)
            .where(
                ScanListing.scan_id == int(scan_id),
                ScanListing.initial_view_count.is_not(None),
                Listing.posted_date_msk == today_msk,
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            )
        )).all())
        ids = [str(listing.external_id) for _snap, listing in rows]
        existing_map = {}
        if ids:
            existing_map = {str(x.external_id): x for x in (await session.execute(
                select(RadarObservation).where(RadarObservation.external_id.in_(ids))
            )).scalars().all()}
        for snap, listing in rows:
            ext = str(listing.external_id)
            measured_at = snap.captured_at or now
            raw = max(0, int(snap.initial_view_count or 0))
            existing = existing_map.get(ext)
            if isinstance(existing, RadarObservation):
                old_enough = (now - (existing.updated_at or existing.last_measured_at or now)).total_seconds() >= 3 * 3600
                if str(existing.status or "") in {"quiet", "expired"} and old_enough:
                    existing.baseline_views = raw
                    existing.baseline_at = measured_at
                    existing.last_views = raw
                    existing.last_measured_at = measured_at
                    existing.checkpoint_count = 0
                    existing.positive_checkpoints = 0
                    existing.consecutive_positive = 0
                    existing.total_delta = 0
                    existing.current_vph = 0.0
                    existing.previous_vph = 0.0
                    existing.peak_vph = 0.0
                    existing.velocity_percentile = 0.0
                    existing.acceleration_ratio = 0.0
                    existing.confidence = 0
                    existing.scored_checkpoints = 0
                    existing.consecutive_scored = 0
                    existing.strong_checkpoints = 0
                    existing.consecutive_strong = 0
                    existing.lease_owner = ""
                    existing.lease_until = None
                    existing.status = "baseline"
                    existing.next_check_at = measured_at + timedelta(minutes=RADAR_V3_FIRST_CHECK_MINUTES)
                    existing.expires_at = measured_at + timedelta(hours=RADAR_V3_MAX_OBSERVATION_HOURS)
                    existing.updated_at = now
                    seeded += 1
                continue
            if ext in existing_map:
                continue
            session.add(RadarObservation(
                external_id=ext, category_key=str(listing.category_key or "unknown"),
                product_key=radar_product_key(listing),
                baseline_views=raw, baseline_at=measured_at,
                last_views=raw, last_measured_at=measured_at,
                checkpoint_count=0, positive_checkpoints=0, consecutive_positive=0,
                total_delta=0, current_vph=0.0, peak_vph=0.0, status="baseline",
                next_check_at=measured_at + timedelta(minutes=RADAR_V3_FIRST_CHECK_MINUTES),
                expires_at=measured_at + timedelta(hours=RADAR_V3_MAX_OBSERVATION_HOURS),
                created_at=now, updated_at=now,
            ))
            existing_map[ext] = True
            seeded += 1
        if seeded:
            await session.commit()
    return seeded


async def radar_v3_due_external_ids(limit: int = 1000) -> list[str]:
    """Read-only diagnostic view of due Radar 3.0 observations."""
    now = datetime.utcnow()
    async with SessionLocal() as session:
        return [str(x) for x in (await session.execute(
            select(RadarObservation.external_id).where(
                RadarObservation.next_check_at.is_not(None),
                RadarObservation.next_check_at <= now,
                RadarObservation.status.in_(["baseline", "candidate", "observed", "confirmed"]),
                or_(RadarObservation.expires_at.is_(None), RadarObservation.expires_at > now),
                or_(RadarObservation.lease_until.is_(None), RadarObservation.lease_until < now),
            ).order_by(RadarObservation.next_check_at.asc()).limit(max(1, int(limit)))
        )).scalars().all()]


async def radar_v3_claim_due_external_ids(owner: str, limit: int = 1000, lease_minutes: int = 20) -> list[str]:
    """Atomically claim due observations for one Parser replica.

    PostgreSQL FOR UPDATE SKIP LOCKED makes concurrent replicas choose disjoint
    rows instead of waiting on each other. The committed lease survives the
    network refresh and automatically expires if a replica dies mid-batch.
    """
    owner = str(owner or "radar3")[:120]
    now = datetime.utcnow()
    lease_until = now + timedelta(minutes=max(5, int(lease_minutes)))
    async with SessionLocal() as session:
        stmt = (
            select(RadarObservation)
            .where(
                RadarObservation.next_check_at.is_not(None),
                RadarObservation.next_check_at <= now,
                RadarObservation.status.in_(["baseline", "candidate", "observed", "confirmed"]),
                or_(RadarObservation.expires_at.is_(None), RadarObservation.expires_at > now),
                or_(RadarObservation.lease_until.is_(None), RadarObservation.lease_until < now),
            )
            .order_by(RadarObservation.next_check_at.asc(), RadarObservation.id.asc())
            .limit(max(1, int(limit)))
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        for row in rows:
            row.lease_owner = owner
            row.lease_until = lease_until
            row.updated_at = now
        await session.commit()
        return [str(row.external_id) for row in rows]


def _percentile_rank(value: float, peers: list[float]) -> float:
    vals = sorted(float(x) for x in peers if x >= 0)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return 0.5
    below = sum(1 for x in vals if x < value)
    equal = sum(1 for x in vals if x == value)
    return max(0.0, min(1.0, (below + 0.5 * equal) / len(vals)))


async def radar_v3_release_claims(owner: str, external_ids: list[str] | tuple[str, ...] | set[str]) -> int:
    """Release unfinished claims so transient View Worker failures retry promptly."""
    ids = list(dict.fromkeys(str(x).strip() for x in external_ids if str(x).strip()))
    if not ids:
        return 0
    async with SessionLocal() as session:
        result = await session.execute(
            update(RadarObservation)
            .where(
                RadarObservation.external_id.in_(ids),
                RadarObservation.lease_owner == str(owner or "")[:120],
            )
            .values(lease_owner="", lease_until=None, updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return int(result.rowcount or 0)


async def radar_v3_record_refreshed(external_ids: list[str] | tuple[str, ...] | set[str]) -> int:
    """Convert exact refreshed counters into Radar 3.1 observed-demand evidence.

    Absolute Demand Gate stays authoritative:
      <15/h   -> Noise/Weak, no Score and observation closes;
      15-29/h -> Candidate, no Score, recheck in 60m;
      30-59/h -> scored Early evidence, recheck in 45m;
      >=60/h  -> Strong first signal, recheck in 30m.

    Above the 30/h Score Gate, DT Score measures context rather than raw movement:
      50% category velocity percentile, 25% persistence, 15% acceleration,
      10% repeatability across independent listings of the same product family.
    Confidence is separate from Score. Hot has two valid paths: two consecutive
    >=60/h intervals on one listing, or strong/persistent evidence confirmed by a
    second independently scored listing in the same product family.
    """
    ids = list(dict.fromkeys(str(x).strip() for x in external_ids if str(x).strip()))
    if not ids:
        return 0
    now = datetime.utcnow()
    prepared: list[tuple[Listing, RadarObservation, int, float, int, float]] = []
    async with SessionLocal() as session:
        pairs = (await session.execute(
            select(Listing, RadarObservation).join(RadarObservation, RadarObservation.external_id == Listing.external_id).where(
                Listing.external_id.in_(ids), Listing.view_count.is_not(None),
                Listing.is_promoted.is_(False), Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            )
        )).all()
        for listing, obs in pairs:
            measured_at = listing.views_checked_at or now
            if obs.expires_at and measured_at > obs.expires_at:
                obs.status = "expired"
                obs.next_check_at = None
                obs.lease_owner = ""
                obs.lease_until = None
                obs.updated_at = now
                continue
            if measured_at <= obs.last_measured_at:
                continue
            current = max(0, int(listing.view_count or 0))
            interval_delta = max(0, current - int(obs.last_views or 0))
            hours = max(1/60, (measured_at - obs.last_measured_at).total_seconds()/3600.0)
            vph = interval_delta / hours
            previous_vph = float(obs.current_vph or 0.0)
            acceleration_ratio = 0.0
            if previous_vph >= RADAR_V3_CANDIDATE_VPH:
                acceleration_ratio = (float(vph) - previous_vph) / max(1.0, previous_vph)

            obs.checkpoint_count = int(obs.checkpoint_count or 0) + 1
            if vph >= RADAR_V3_CANDIDATE_VPH:
                obs.positive_checkpoints = int(obs.positive_checkpoints or 0) + 1
                obs.consecutive_positive = int(obs.consecutive_positive or 0) + 1
            else:
                obs.consecutive_positive = 0
            if vph >= RADAR_V3_SCORE_VPH:
                obs.scored_checkpoints = int(obs.scored_checkpoints or 0) + 1
                obs.consecutive_scored = int(obs.consecutive_scored or 0) + 1
            else:
                obs.consecutive_scored = 0
            if vph >= RADAR_V3_STRONG_VPH:
                obs.strong_checkpoints = int(obs.strong_checkpoints or 0) + 1
                obs.consecutive_strong = int(obs.consecutive_strong or 0) + 1
            else:
                obs.consecutive_strong = 0

            obs.last_views = current
            obs.last_measured_at = measured_at
            obs.total_delta = max(0, current - int(obs.baseline_views or 0))
            obs.previous_vph = previous_vph
            obs.current_vph = float(vph)
            obs.acceleration_ratio = float(acceleration_ratio)
            obs.peak_vph = max(float(obs.peak_vph or 0.0), float(vph))
            obs.updated_at = now
            obs.lease_owner = ""
            obs.lease_until = None

            if vph < RADAR_V3_CANDIDATE_VPH:
                obs.status = "quiet"
                obs.next_check_at = None
            elif vph < RADAR_V3_SCORE_VPH:
                obs.status = "candidate"
                obs.next_check_at = measured_at + timedelta(minutes=RADAR_V3_NEXT_CHECK_MINUTES)
            elif vph >= RADAR_V3_STRONG_VPH or int(obs.consecutive_scored or 0) >= 2:
                obs.status = "confirmed"
                obs.next_check_at = measured_at + timedelta(minutes=RADAR_V3_STRONG_CHECK_MINUTES)
            else:
                obs.status = "observed"
                obs.next_check_at = measured_at + timedelta(minutes=RADAR_V3_EARLY_CHECK_MINUTES)
            if obs.expires_at and obs.next_check_at and obs.next_check_at > obs.expires_at:
                obs.next_check_at = None
            prepared.append((listing, obs, interval_delta, vph, current, previous_vph))
        await session.commit()

    emitted = 0
    for listing, obs, interval_delta, vph, current, previous_vph in prepared:
        if vph < RADAR_V3_SCORE_VPH:
            continue
        async with SessionLocal() as session:
            peers = [float(x or 0.0) for x in (await session.execute(select(RadarObservation.current_vph).where(
                RadarObservation.category_key == str(obs.category_key),
                RadarObservation.checkpoint_count >= 1,
                RadarObservation.current_vph >= RADAR_V3_CANDIDATE_VPH,
                RadarObservation.status.in_(["candidate", "observed", "confirmed"]),
                or_(RadarObservation.expires_at.is_(None), RadarObservation.expires_at > now),
                RadarObservation.updated_at >= now - timedelta(hours=6),
            ))).scalars().all()]
            family = list((await session.execute(select(RadarObservation).where(
                RadarObservation.product_key == str(obs.product_key),
                RadarObservation.current_vph >= RADAR_V3_CANDIDATE_VPH,
                RadarObservation.positive_checkpoints >= 1,
                RadarObservation.status.in_(["candidate", "observed", "confirmed"]),
                or_(RadarObservation.expires_at.is_(None), RadarObservation.expires_at > now),
                RadarObservation.updated_at >= now - timedelta(hours=6),
            ))).scalars().all())

        category_percentile = _percentile_rank(float(vph), peers)
        peer_count = len(peers)
        family_candidate = len({str(x.external_id) for x in family})
        family_scored = len({str(x.external_id) for x in family if float(x.current_vph or 0.0) >= RADAR_V3_SCORE_VPH})
        family_strong = len({str(x.external_id) for x in family if float(x.current_vph or 0.0) >= RADAR_V3_STRONG_VPH})

        velocity_points = round(50 * category_percentile)
        consecutive_scored = int(obs.consecutive_scored or 0)
        persistence_points = 25 if consecutive_scored >= 3 else (15 if consecutive_scored >= 2 else 0)
        accel = float(obs.acceleration_ratio or 0.0)
        if previous_vph < RADAR_V3_CANDIDATE_VPH:
            acceleration_points = 0
        elif accel >= 0.50:
            acceleration_points = 15
        elif accel >= 0.20:
            acceleration_points = 12
        elif accel >= 0.0:
            acceleration_points = 8
        elif accel >= -0.15:
            acceleration_points = 4
        else:
            acceleration_points = 0
        repeat_points = 10 if family_scored >= 2 else (5 if family_candidate >= 2 else 0)
        score = max(1, min(100, int(velocity_points + persistence_points + acceleration_points + repeat_points)))
        # One interval may reveal a strong lead, but it cannot pretend to have
        # persistence/acceleration evidence that DT has not observed yet.
        if int(obs.scored_checkpoints or 0) <= 1:
            score = min(score, 50)

        confidence = 30
        confidence += min(20, peer_count)
        confidence += 20 if int(obs.scored_checkpoints or 0) >= 2 else 0
        confidence += 10 if int(obs.strong_checkpoints or 0) >= 1 else 0
        confidence += 10 if family_scored >= 2 else 0
        confidence += 5 if family_strong >= 2 else 0
        confidence = max(0, min(95, int(confidence)))

        # Hot path A: one listing sustains >=60/h twice.
        solo_hot = int(obs.consecutive_strong or 0) >= 2
        # Hot path B: a strong/persistent listing is independently confirmed by
        # another family listing that also crossed the 30/h Score Gate.
        family_hot = family_scored >= 2 and (float(vph) >= RADAR_V3_STRONG_VPH or consecutive_scored >= 2)
        if solo_hot or family_hot:
            demand_status, stage = "hot", "product_hot"
        elif float(vph) >= RADAR_V3_STRONG_VPH or consecutive_scored >= 2:
            demand_status, stage = "rising", "confirmed"
        else:
            demand_status, stage = "stable", "observed"

        # Persist context metrics even before signal write so the dashboard can
        # explain how Radar reached the decision.
        async with SessionLocal() as session:
            await session.execute(
                update(RadarObservation).where(RadarObservation.external_id == str(obs.external_id)).values(
                    velocity_percentile=float(category_percentile),
                    confidence=int(confidence),
                    updated_at=now,
                )
            )
            await session.commit()

        allowed, detail_reason, verified_at = await _live_detail_organic_gate(listing, force_priority="radar_checkpoint")
        if not allowed:
            continue
        elapsed_hours = max(1/60, (obs.last_measured_at - obs.baseline_at).total_seconds()/3600.0)
        result = await _upsert_signal(
            source_key=f"radar3:{obs.external_id}:{int(obs.last_measured_at.timestamp())}",
            source="radar3_observed", listing=listing, product_key=str(obs.product_key),
            score=score, confidence=confidence,
            stage=stage, opportunity_type="observed_demand", view_count=current, views_per_hour=float(vph),
            reasons=[
                f"Radar 3.1: {float(vph):.1f}/h · category P{int(round(category_percentile*100))} among {peer_count} live peers",
                f"Score = velocity {velocity_points}/50 + persistence {persistence_points}/25 + acceleration {acceleration_points}/15 + repeatability {repeat_points}/10",
                f"Confidence {confidence}% · scored checkpoints={int(obs.scored_checkpoints or 0)} · strong checkpoints={int(obs.strong_checkpoints or 0)}",
                f"Acceleration {accel*100:+.0f}% vs previous {previous_vph:.1f}/h · current interval +{int(interval_delta)}",
                f"Family confirmations: Candidate {family_candidate} · Score Gate {family_scored} · Strong {family_strong}",
                f"DT observed +{int(obs.total_delta)} views in {elapsed_hours:.1f}h after baseline; Initial counter is baseline-only and contributed 0 points",
            ],
            recorded_at=obs.last_measured_at, live_detail_verified_at=verified_at,
            demand_views=int(obs.total_delta), demand_age_minutes=float(elapsed_hours*60), radar_rank=float(score),
            demand_status=demand_status, demand_gate=int(RADAR_V3_SCORE_VPH),
        )
        if result is not None:
            emitted += 1
    return emitted


async def radar_v3_expire_observations() -> int:
    """Expire active observation rows whose DT-owned measurement window ended."""
    now = datetime.utcnow()
    async with SessionLocal() as session:
        result = await session.execute(
            update(RadarObservation)
            .where(
                RadarObservation.status.in_(["baseline", "candidate", "observed", "confirmed"]),
                RadarObservation.expires_at.is_not(None),
                RadarObservation.expires_at <= now,
            )
            .values(
                status="expired", next_check_at=None, lease_owner="", lease_until=None, updated_at=now
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return int(result.rowcount or 0)


async def radar_v3_expire_stale_products(max_age_hours: int = 6) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(max_age_hours)))
    async with SessionLocal() as session:
        result = await session.execute(
            update(RadarProduct).where(
                RadarProduct.latest_source == "radar3_observed",
                RadarProduct.last_signal_at < cutoff,
                RadarProduct.status != "historical",
            ).values(status="historical", current_score=0, radar_rank=0.0, updated_at=datetime.utcnow())
        )
        await session.commit()
        return int(result.rowcount or 0)


async def prepare_radar_v3_once() -> bool:
    """One-time clean break, serialized across Parser replicas."""
    async with SessionLocal() as session:
        bind = session.get_bind()
        if bind is not None and str(bind.dialect.name).startswith("postgres"):
            # Transaction-scoped lock makes rolling deploys safe: only one replica
            # can test/purge/set the reset marker at a time.
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": RADAR_V3_RESET_SETTING})
        existing = await session.get(AppSetting, RADAR_V3_RESET_SETTING)
        if existing is not None and str(existing.value or "") == "done":
            return False
        await session.execute(delete(RadarFavorite))
        await session.execute(delete(RadarLifecycleWatch))
        await session.execute(delete(RadarSnapshot))
        await session.execute(delete(RadarProductListing))
        await session.execute(delete(RadarProduct))
        await session.execute(delete(RadarObservation))
        if existing is None:
            session.add(AppSetting(key=RADAR_V3_RESET_SETTING, value="done"))
        else:
            existing.value = "done"
        await session.commit()
    log.warning("DT Radar 3.0 reset complete: old Radar products/signals removed; listing/view history preserved")
    return True


async def record_autoscan_hot(
    round_id: str,
    category_key: str,
    matched_ids: list[str] | tuple[str, ...] | set[str],
    *,
    limit: int = RADAR_SCAN_TOP_LIMIT,
) -> int:
    """Compatibility wrapper returning only the number of saved signals."""
    return int((await record_autoscan_hot_detailed(
        round_id, category_key, matched_ids, limit=limit
    )).saved)


async def record_verified_velocity_signals(
    external_ids: list[str] | tuple[str, ...] | set[str], *, traffic_priority: str = "normal"
) -> int:
    """Admit newly certified 400+ baselines using only their observed organic delta."""
    # Radar 3.0: legacy admission path disabled; only DT-observed checkpoints may publish.
    return 0
    ids = list(dict.fromkeys(str(x).strip() for x in external_ids if str(x).strip()))
    if not ids:
        return 0
    async with SessionLocal() as session:
        targets = list((await session.execute(
            select(Listing).where(
                Listing.external_id.in_(ids),
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                Listing.organic_baseline_views >= int(ORGANIC_HIGH_BASELINE_VIEWS),
                Listing.organic_verified_checkpoints >= int(ORGANIC_HIGH_REQUIRED_CHECKPOINTS),
                Listing.organic_history_status == "observed",
                Listing.view_count.is_not(None),
                ~_registry_dirty_exists(Listing.external_id),
            )
        )).scalars().all())
    if not targets:
        return 0
    by_category: dict[str, set[str]] = {}
    for row in targets:
        by_category.setdefault(str(row.category_key or "unknown"), set()).add(str(row.external_id))

    saved = 0
    for category_key, target_ids in by_category.items():
        scored, cohort_size = await _score_unified_48h_category(category_key, target_ids)
        for external_id in target_ids:
            data = scored.get(external_id)
            if data is None:
                continue
            listing, feature, score, metric_kind = data
            if metric_kind != "observed_delta":
                continue
            evidence = classify_radar_signal(
                dt_score=int(score.score or 0), confidence=int(score.confidence or 0),
                demand_views=int(feature.views), age_minutes=float(feature.age_minutes),
            )
            if not evidence.admitted:
                log.info(
                    "Verified velocity below unified admission external_id=%s score=%s demand=%s gate=%s",
                    external_id, score.score, feature.views, evidence.demand_gate,
                )
                continue
            allowed, reason, verified_at = await _live_detail_organic_gate(
                listing, force_priority=("background" if traffic_priority == "background" else "normal")
            )
            if not allowed:
                log.info("Verified velocity Radar admission blocked external_id=%s reason=%s", external_id, reason)
                continue
            baseline_at = getattr(listing, "organic_baseline_at", None)
            baseline_token = int(baseline_at.timestamp()) if baseline_at is not None else 0
            result = await _upsert_signal(
                source_key=f"verified-velocity:{external_id}:{baseline_token}",
                source="verified_velocity",
                listing=listing,
                product_key=radar_product_key(listing, score.cohort_key),
                score=int(score.score),
                confidence=int(score.confidence or 0),
                stage=str(score.stage or "rising"),
                opportunity_type=str(score.opportunity_type or "spark"),
                view_count=int(listing.view_count or 0),
                views_per_hour=float(score.views_per_hour or 0.0),
                reasons=[
                    f"Verified Organic Velocity: +{int(feature.views)} after baseline {int(listing.organic_baseline_views or 0)}",
                    f"Unified 48H: {evidence.status} · Score {int(score.score)} · Rank {float(evidence.radar_rank):.1f}",
                    f"Demand Gate: {int(feature.views)}/{int(evidence.demand_gate)} · cohort {cohort_size}",
                    *[str(x) for x in tuple(score.reasons or ())[:2]],
                ],
                recorded_at=listing.views_checked_at or datetime.utcnow(),
                live_detail_verified_at=verified_at,
                demand_views=int(feature.views),
                demand_age_minutes=float(feature.age_minutes),
                radar_rank=float(evidence.radar_rank),
                demand_status=str(evidence.status),
                demand_gate=int(evidence.demand_gate),
            )
            if result is not None:
                saved += 1
                log.info(
                    "Verified Organic Velocity entered unified Radar external_id=%s score=%s rank=%.1f delta=%s vph=%.2f",
                    external_id, score.score, float(evidence.radar_rank), feature.views, score.views_per_hour,
                )
    return saved


async def record_ai_candidate(candidate_id: int, *, source_key: str | None = None, source: str = "ai") -> int | None:
    """Merge the latest AI state into Radar. Control candidates never enter Radar."""
    # Radar 3.0: legacy admission path disabled; only DT-observed checkpoints may publish.
    return None
    async with SessionLocal() as session:
        candidate = await session.get(AIEarlyWinnerCandidate, int(candidate_id))
        if candidate is None or candidate.is_control:
            return None
        listing = (await session.execute(
            select(Listing).where(
                Listing.external_id == candidate.external_id,
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
            ).limit(1)
        )).scalar_one_or_none()
        if listing is None:
            return None
        if high_baseline_pending(listing):
            log.info(
                "DT Radar withheld AI candidate=%s external_id=%s reason=high_baseline_pending baseline=%s checkpoints=%s",
                candidate_id, listing.external_id, getattr(listing, "organic_baseline_views", None),
                int(getattr(listing, "organic_verified_checkpoints", 0) or 0),
            )
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
                _clean_listing_exists(RadarLifecycleWatch.external_id),
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

        allowed, gate_reason = await _strict_organic_gate(session, str(watch.external_id or ""))
        if not allowed:
            # Lifecycle creates its Fast Sold snapshot directly, so it must pass
            # the exact same DB-authoritative gate as normal Radar ingestion.
            watch.status = "excluded"
            watch.next_check_at = None
            watch.last_result = f"nonorganic:{gate_reason}"[:80]
            watch.lease_owner = ""
            watch.lease_until = None
            watch.updated_at = now
            await session.commit()
            log.warning(
                "Strict Organic Radar Gate excluded lifecycle external_id=%s reason=%s",
                watch.external_id, gate_reason,
            )
            return "excluded"

        watch.checks = int(watch.checks or 0) + 1
        watch.last_checked_at = now
        watch.last_error = (str(error_text)[:1000] if error_text else None)
        watch.lease_owner = ""
        watch.lease_until = None
        watch.updated_at = now

        listing = (await session.execute(
            select(Listing).where(Listing.external_id == str(watch.external_id)).limit(1)
        )).scalar_one_or_none()
        if listing is not None and (
            bool(getattr(listing, "is_promoted", False))
            or bool(getattr(listing, "is_price_reduced", False))
        ):
            # A contamination flag can arrive while a Lifecycle check is leased.
            # Never let that race create a Fast Sold signal from non-organic demand.
            watch.status = "excluded"
            watch.next_check_at = None
            watch.last_result = "nonorganic"
            watch.lease_owner = ""
            watch.lease_until = None
            watch.updated_at = now
            await session.commit()
            return "excluded"

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
                _clean_listing_exists(RadarLifecycleWatch.external_id),
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
    """Refresh the 48H rank/status without letting score alone manufacture Hot."""
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
                signal_age_hours = (
                    max(0.0, (now - product.last_signal_at).total_seconds() / 3600.0)
                    if product.last_signal_at is not None else 10**6
                )
                if signal_age_hours > 48.0:
                    new_status = "historical"
                    new_rank = 0.0
                else:
                    effective_age_minutes = (
                        float(getattr(product, "demand_age_minutes", 0.0) or 0.0)
                        + signal_age_hours * 60.0
                    )
                    evidence = classify_radar_signal(
                        dt_score=new_score, confidence=int(product.confidence or 0),
                        demand_views=int(getattr(product, "demand_views", 0) or 0),
                        age_minutes=effective_age_minutes,
                    )
                    new_status = str(evidence.status)
                    new_rank = float(evidence.radar_rank) if evidence.admitted else 0.0
                    product.demand_gate = int(evidence.demand_gate if evidence.demand_gate < 10**9 else 0)
                if (
                    int(product.current_score or 0) != new_score
                    or product.status != new_status
                    or abs(float(getattr(product, "radar_rank", 0.0) or 0.0) - new_rank) > 1e-6
                ):
                    product.current_score = new_score
                    product.status = new_status
                    product.radar_rank = new_rank
                    product.updated_at = now
                    changed += 1
            if changed:
                await session.commit()
    return changed


async def radar_stats() -> RadarStats:
    async with SessionLocal() as session:
        # v4.15.3 read gate: even if cleanup is milliseconds behind a new sticky
        # flag, public/admin Radar counters must only expose product families with
        # at least one currently DB-confirmed organic association.
        visible = _visible_product_association_exists(RadarProduct.id)
        visible_product_ids = select(RadarProduct.id).where(visible)
        total = int((await session.execute(
            select(func.count(RadarProduct.id)).where(visible)
        )).scalar_one() or 0)
        hot = int((await session.execute(select(func.count(RadarProduct.id)).where(
            visible, RadarProduct.status == "hot"
        ))).scalar_one() or 0)
        rising = int((await session.execute(select(func.count(RadarProduct.id)).where(
            visible, RadarProduct.status == "rising"
        ))).scalar_one() or 0)
        ai_picks = int((await session.execute(select(func.count(RadarProduct.id)).where(
            visible,
            RadarProduct.status.in_(["hot", "rising"]),
            RadarProduct.opportunity_type.in_(["hot_product", "hidden_gem", "emerging"]),
            RadarProduct.confidence >= 55,
        ))).scalar_one() or 0)
        categories = int((await session.execute(select(func.count(func.distinct(RadarProduct.category_key))).where(
            visible
        ))).scalar_one() or 0)
        signals = int((await session.execute(select(func.count(RadarSnapshot.id)).where(
            RadarSnapshot.product_id.in_(visible_product_ids),
            _clean_listing_exists(RadarSnapshot.external_id),
        ))).scalar_one() or 0)
        fast_sold = int((await session.execute(
            select(func.count(func.distinct(RadarLifecycleWatch.product_id))).where(
                RadarLifecycleWatch.product_id.in_(visible_product_ids),
                _clean_listing_exists(RadarLifecycleWatch.external_id),
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
        conditions = [_visible_product_association_exists(RadarProduct.id)]
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
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            ]
            if price_lo is not None:
                price_conditions.append(RadarProductListing.last_price_eur >= int(price_lo))
            if price_hi is not None:
                price_conditions.append(RadarProductListing.last_price_eur <= int(price_hi))
            conditions.append(
                select(RadarProductListing.id)
                .join(Listing, Listing.external_id == RadarProductListing.external_id)
                .where(*price_conditions)
                .exists()
            )
        if mode == "hot":
            conditions.append(RadarProduct.status == "hot")
            order = (RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "rising":
            conditions.append(RadarProduct.status == "rising")
            order = (RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "ai":
            conditions.extend([
                RadarProduct.status.in_(["hot", "rising"]),
                RadarProduct.opportunity_type.in_(["hot_product", "hidden_gem", "emerging"]),
                RadarProduct.confidence >= 55,
            ])
            order = (RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.confidence.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "fastsold":
            fast_product_ids = select(RadarLifecycleWatch.product_id).where(
                RadarLifecycleWatch.status == "disappeared",
                RadarLifecycleWatch.lifetime_seconds.is_not(None),
                RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
                _clean_listing_exists(RadarLifecycleWatch.external_id),
            )
            conditions.append(RadarProduct.id.in_(fast_product_ids))
            latest_disappearance = (
                select(func.max(RadarLifecycleWatch.disappeared_at))
                .where(
                    RadarLifecycleWatch.product_id == RadarProduct.id,
                    RadarLifecycleWatch.status == "disappeared",
                    RadarLifecycleWatch.lifetime_seconds.is_not(None),
                    RadarLifecycleWatch.lifetime_seconds <= RADAR_FAST_SOLD_MAX_SECONDS,
                    _clean_listing_exists(RadarLifecycleWatch.external_id),
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
                    _clean_listing_exists(RadarLifecycleWatch.external_id),
                )
                .correlate(RadarProduct).scalar_subquery()
            )
            order = (latest_disappearance.desc(), fastest_lifetime.asc(), RadarProduct.peak_score.desc())
        elif mode == "alltime":
            order = (RadarProduct.peak_score.desc(), RadarProduct.signal_count.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "favorites" and user_id is not None:
            fav_ids = select(RadarFavorite.product_id).where(RadarFavorite.user_id == int(user_id))
            conditions.append(RadarProduct.id.in_(fav_ids))
            order = (RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "category_best" and category_key:
            order = (RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.first_radar_at.desc(), RadarProduct.last_signal_at.desc())
        elif mode == "category_new" and category_key:
            order = (RadarProduct.first_radar_at.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
        else:
            order = (RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
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
        conditions = [
            RadarProduct.title.ilike(pattern),
            _visible_product_association_exists(RadarProduct.id),
        ]
        price_lo, price_hi = price_bounds(price_filter)
        if price_lo is not None or price_hi is not None:
            price_conditions = [
                RadarProductListing.product_id == RadarProduct.id,
                RadarProductListing.last_price_eur.is_not(None),
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            ]
            if price_lo is not None:
                price_conditions.append(RadarProductListing.last_price_eur >= int(price_lo))
            if price_hi is not None:
                price_conditions.append(RadarProductListing.last_price_eur <= int(price_hi))
            conditions.append(
                select(RadarProductListing.id)
                .join(Listing, Listing.external_id == RadarProductListing.external_id)
                .where(*price_conditions)
                .exists()
            )
        total = int((await session.execute(
            select(func.count(RadarProduct.id)).where(*conditions)
        )).scalar_one() or 0)
        rows = list((await session.execute(
            select(RadarProduct)
            .where(*conditions)
            .order_by(RadarProduct.radar_rank.desc(), RadarProduct.current_score.desc(), RadarProduct.last_signal_at.desc())
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
            .where(
                RadarProduct.category_key != "",
                _visible_product_association_exists(RadarProduct.id),
            )
            .group_by(RadarProduct.category_key)
            .order_by(func.count(RadarProduct.id).desc(), func.max(RadarProduct.current_score).desc())
        )).all()
    return [
        (str(key), int(total or 0), int(new_today or 0), int(score or 0))
        for key, total, new_today, score in rows
    ]


async def get_radar_product(product_id: int) -> tuple[RadarProduct | None, Listing | None, list[RadarSnapshot]]:
    async with SessionLocal() as session:
        product = (await session.execute(
            select(RadarProduct).where(
                RadarProduct.id == int(product_id),
                _visible_product_association_exists(RadarProduct.id),
            ).limit(1)
        )).scalar_one_or_none()
        if product is None:
            return None, None, []
        listing = None
        if product.representative_external_id:
            listing = (await session.execute(select(Listing).where(
                Listing.external_id == product.representative_external_id,
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            ).limit(1))).scalar_one_or_none()
        if listing is None:
            # Defensive fallback for a product being opened during/just before an
            # integrity cleanup: choose another surviving organic association.
            listing = (await session.execute(
                select(Listing)
                .join(RadarProductListing, RadarProductListing.external_id == Listing.external_id)
                .where(
                    RadarProductListing.product_id == int(product_id),
                    Listing.is_promoted.is_(False),
                    Listing.is_price_reduced.is_(False),
                    ~_registry_dirty_exists(Listing.external_id),
                )
                .order_by(RadarProductListing.last_seen_at.desc())
                .limit(1)
            )).scalar_one_or_none()
        snapshots = list((await session.execute(
            select(RadarSnapshot)
            .join(Listing, Listing.external_id == RadarSnapshot.external_id)
            .where(
                RadarSnapshot.product_id == int(product_id),
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            )
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


async def purge_nonorganic_analytics(
    external_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    *,
    infer_historical_price_drops: bool = False,
) -> dict[str, int]:
    """Remove paid/reduced listings from AI + Radar analytical history.

    The underlying Listing/PriceHistory/ViewHistory rows are intentionally kept for
    audit/debugging, but every demand-learning surface is rebuilt from organic rows.
    ``is_promoted`` and ``is_price_reduced`` are sticky contamination flags.
    """
    stats = {
        "dirty_listings": 0, "ai_candidates": 0, "radar_snapshots": 0,
        "radar_links": 0, "lifecycle_watches": 0, "radar_products_removed": 0,
        "radar_products_rebuilt": 0,
    }
    now = datetime.utcnow()
    async with _radar_lock:
        async with SessionLocal() as session:
            if infer_historical_price_drops:
                # Infer old reductions from raw price history. Window LAG is supported
                # by PostgreSQL and modern SQLite used in local tests. A reduction is
                # sticky: even if a seller later raises the price again, its accumulated
                # views are no longer a clean organic-demand sample.
                await session.execute(text(
                    """
                    UPDATE listings
                    SET is_price_reduced = TRUE
                    WHERE COALESCE(is_price_reduced, FALSE) = FALSE
                      AND external_id IN (
                        SELECT external_id FROM (
                          SELECT external_id, price_eur,
                                 LAG(price_eur) OVER (
                                   PARTITION BY external_id
                                   ORDER BY recorded_at, id
                                 ) AS previous_price
                          FROM price_history
                          WHERE price_eur IS NOT NULL
                        ) price_steps
                        WHERE previous_price IS NOT NULL
                          AND price_eur < previous_price
                      )
                    """
                ))

            requested: set[str] | None = None
            if external_ids is not None:
                requested = {str(x).strip() for x in external_ids if str(x).strip()}
                if not requested:
                    return stats

            # v4.15.3: the sticky registry is a first-class source of truth. Older
            # or cross-process rows can theoretically have a dirty registry entry
            # while listings still says FALSE/FALSE. Cleanup must union both sides
            # and repair them before rebuilding analytical state.
            listing_query = select(Listing).where(
                (Listing.is_promoted.is_(True)) | (Listing.is_price_reduced.is_(True))
            )
            registry_query = select(ListingIntegrity).where(
                (ListingIntegrity.is_promoted.is_(True)) | (ListingIntegrity.is_price_reduced.is_(True))
            )
            if requested is not None:
                ordered_requested = sorted(requested)
                listing_query = listing_query.where(Listing.external_id.in_(ordered_requested))
                registry_query = registry_query.where(ListingIntegrity.external_id.in_(ordered_requested))

            dirty_listing_rows = list((await session.execute(listing_query)).scalars().all())
            dirty_registry_rows = list((await session.execute(registry_query)).scalars().all())
            dirty_ids = sorted({
                str(row.external_id) for row in [*dirty_listing_rows, *dirty_registry_rows]
                if str(getattr(row, "external_id", "") or "").strip()
            })
            stats["dirty_listings"] = len(dirty_ids)
            if not dirty_ids:
                await session.commit()
                return stats

            # Load all matching rows, not just the side that originally exposed the
            # contamination, then make Listing and listing_integrity agree on the
            # sticky OR of both flags. This repair is idempotent.
            all_listing_rows = list((await session.execute(
                select(Listing).where(Listing.external_id.in_(dirty_ids))
            )).scalars().all())
            all_registry_rows = list((await session.execute(
                select(ListingIntegrity).where(ListingIntegrity.external_id.in_(dirty_ids))
            )).scalars().all())
            listings_by_id = {str(row.external_id): row for row in all_listing_rows}
            registry = {str(row.external_id): row for row in all_registry_rows}
            for external_id in dirty_ids:
                listing_row = listings_by_id.get(external_id)
                entry = registry.get(external_id)
                is_promoted = bool(
                    (getattr(listing_row, "is_promoted", False) if listing_row is not None else False)
                    or (getattr(entry, "is_promoted", False) if entry is not None else False)
                )
                is_price_reduced = bool(
                    (getattr(listing_row, "is_price_reduced", False) if listing_row is not None else False)
                    or (getattr(entry, "is_price_reduced", False) if entry is not None else False)
                )
                if listing_row is not None:
                    listing_row.is_promoted = is_promoted
                    listing_row.is_price_reduced = is_price_reduced
                if entry is None:
                    entry = ListingIntegrity(
                        external_id=external_id,
                        is_promoted=is_promoted,
                        is_price_reduced=is_price_reduced,
                        first_detected_at=now,
                        last_detected_at=now,
                    )
                    session.add(entry)
                    registry[external_id] = entry
                else:
                    entry.is_promoted = bool(entry.is_promoted or is_promoted)
                    entry.is_price_reduced = bool(entry.is_price_reduced or is_price_reduced)
                    entry.last_detected_at = now

            candidate_rows = (await session.execute(
                select(AIEarlyWinnerCandidate.id, AIEarlyWinnerCandidate.run_id).where(
                    AIEarlyWinnerCandidate.external_id.in_(dirty_ids)
                )
            )).all()
            candidate_ids = sorted({int(row[0]) for row in candidate_rows})
            run_ids = sorted({int(row[1]) for row in candidate_rows})
            if candidate_ids:
                await session.execute(delete(AIEarlyWinnerObservation).where(
                    AIEarlyWinnerObservation.candidate_id.in_(candidate_ids)
                ))
                await session.execute(delete(AIEarlyWinnerEvent).where(
                    AIEarlyWinnerEvent.candidate_id.in_(candidate_ids)
                ))
                deleted_candidates = await session.execute(delete(AIEarlyWinnerCandidate).where(
                    AIEarlyWinnerCandidate.id.in_(candidate_ids)
                ))
                stats["ai_candidates"] = int(deleted_candidates.rowcount or 0)

            affected_product_ids = set((await session.execute(
                select(RadarSnapshot.product_id).where(RadarSnapshot.external_id.in_(dirty_ids))
            )).scalars().all())
            affected_product_ids.update((await session.execute(
                select(RadarProductListing.product_id).where(RadarProductListing.external_id.in_(dirty_ids))
            )).scalars().all())
            affected_product_ids.update((await session.execute(
                select(RadarLifecycleWatch.product_id).where(RadarLifecycleWatch.external_id.in_(dirty_ids))
            )).scalars().all())

            deleted_snapshots = await session.execute(delete(RadarSnapshot).where(
                RadarSnapshot.external_id.in_(dirty_ids)
            ))
            stats["radar_snapshots"] = int(deleted_snapshots.rowcount or 0)
            deleted_links = await session.execute(delete(RadarProductListing).where(
                RadarProductListing.external_id.in_(dirty_ids)
            ))
            stats["radar_links"] = int(deleted_links.rowcount or 0)
            deleted_watches = await session.execute(delete(RadarLifecycleWatch).where(
                RadarLifecycleWatch.external_id.in_(dirty_ids)
            ))
            stats["lifecycle_watches"] = int(deleted_watches.rowcount or 0)

            # Keep admin run counters consistent after candidate removal.
            for run_id in run_ids:
                run = await session.get(AIEarlyWinnerRun, int(run_id))
                if run is None:
                    continue
                remaining = list((await session.execute(
                    select(AIEarlyWinnerCandidate.is_control).where(
                        AIEarlyWinnerCandidate.run_id == int(run_id)
                    )
                )).scalars().all())
                run.candidate_count = sum(1 for x in remaining if not bool(x))
                run.control_count = sum(1 for x in remaining if bool(x))

            # Rebuild every touched Radar family from surviving clean snapshots.
            for product_id in sorted(int(x) for x in affected_product_ids if x is not None):
                product = await session.get(RadarProduct, product_id)
                if product is None:
                    continue
                snapshots = list((await session.execute(
                    select(RadarSnapshot).where(RadarSnapshot.product_id == product_id)
                    .order_by(RadarSnapshot.recorded_at.desc(), RadarSnapshot.id.desc())
                )).scalars().all())
                associations = list((await session.execute(
                    select(RadarProductListing).where(RadarProductListing.product_id == product_id)
                )).scalars().all())
                if not snapshots:
                    await session.execute(delete(RadarFavorite).where(RadarFavorite.product_id == product_id))
                    await session.execute(delete(RadarLifecycleWatch).where(RadarLifecycleWatch.product_id == product_id))
                    await session.execute(delete(RadarProductListing).where(RadarProductListing.product_id == product_id))
                    await session.delete(product)
                    stats["radar_products_removed"] += 1
                    continue

                latest_by_listing: dict[str, RadarSnapshot] = {}
                for snap in snapshots:
                    ext = str(snap.external_id or f"snapshot:{snap.id}")
                    if ext not in latest_by_listing:
                        latest_by_listing[ext] = snap
                cutoff_48h = now - timedelta(hours=48)
                live_ranked = []
                for snap in latest_by_listing.values():
                    if snap.recorded_at is None or snap.recorded_at < cutoff_48h:
                        continue
                    live_evidence = _snapshot_live_evidence(snap, now)
                    if live_evidence.admitted:
                        live_ranked.append((snap, live_evidence))
                if live_ranked:
                    strongest, strongest_evidence = max(
                        live_ranked,
                        key=lambda pair: (float(pair[1].radar_rank), int(pair[0].score or 0), pair[0].recorded_at or datetime.min),
                    )
                else:
                    strongest = None
                    strongest_evidence = None
                newest = max(snapshots, key=lambda x: (x.recorded_at or datetime.min, int(x.id or 0)))

                product.signal_count = len(snapshots)
                product.confirmed_count = len({
                    int(x.candidate_id) for x in snapshots
                    if x.candidate_id is not None and str(x.outcome or "") == "confirmed"
                })
                product.listing_count = len(associations)
                product.peak_score = max(int(x.score or 0) for x in snapshots)
                if strongest is not None:
                    product.last_signal_score = int(strongest.score or 0)
                    product.last_signal_at = strongest.recorded_at
                    product.confidence = int(strongest.confidence or 0)
                    product.radar_rank = float(strongest_evidence.radar_rank)
                    product.demand_views = int(getattr(strongest, "demand_views", 0) or 0)
                    product.demand_age_minutes = float(getattr(strongest, "demand_age_minutes", 0.0) or 0.0)
                    product.demand_gate = int(strongest_evidence.demand_gate if strongest_evidence.demand_gate < 10**9 else 0)
                    product.opportunity_type = str(strongest.opportunity_type or "spark")[:32]
                    product.latest_source = str(strongest.source or "")[:32]
                    product.last_ai_candidate_id = strongest.candidate_id
                    try:
                        reasons = json.loads(strongest.reasons_json or "[]")
                        product.latest_reason = str(reasons[0] if isinstance(reasons, list) and reasons else "")[:800]
                    except Exception:
                        product.latest_reason = ""
                    product.representative_external_id = str(strongest.external_id or "")
                else:
                    product.last_signal_score = 0
                    product.confidence = 0
                    product.radar_rank = 0.0
                    product.demand_views = 0
                    product.demand_age_minutes = 0.0
                    product.demand_gate = 0
                    product.latest_source = str(newest.source or "")[:32]
                    product.last_ai_candidate_id = newest.candidate_id
                    product.latest_reason = ""
                product.best_views = max(
                    [int(x.best_views or 0) for x in associations]
                    + [int(x.view_count or 0) for x in snapshots]
                    + [0]
                )
                product.best_views_per_hour = max([float(x.views_per_hour or 0.0) for x in snapshots] + [0.0])
                prices = [int(x.last_price_eur) for x in associations if x.last_price_eur is not None]
                if not prices:
                    prices = [int(x.price_eur) for x in snapshots if x.price_eur is not None]
                product.min_price_eur = min(prices) if prices else None
                product.max_price_eur = max(prices) if prices else None
                if associations:
                    product.first_seen_at = min(x.first_seen_at for x in associations if x.first_seen_at is not None)
                    product.last_seen_at = max(x.last_seen_at for x in associations if x.last_seen_at is not None)
                if strongest is not None:
                    product.current_score = _effective_score(product, now)
                    product.status = str(strongest_evidence.status)
                else:
                    product.current_score = 0
                    product.status = "historical"
                    product.radar_rank = 0.0
                product.updated_at = now
                stats["radar_products_rebuilt"] += 1

            await session.commit()

    if stats["dirty_listings"]:
        log.warning(
            "Organic Demand cleanup dirty=%s ai=%s radar_snapshots=%s radar_links=%s lifecycle=%s products_removed=%s products_rebuilt=%s",
            stats["dirty_listings"], stats["ai_candidates"], stats["radar_snapshots"],
            stats["radar_links"], stats["lifecycle_watches"],
            stats["radar_products_removed"], stats["radar_products_rebuilt"],
        )
    return stats


async def prepare_unified_48h_ranking_once() -> dict[str, int]:
    """One-time cleanup of pre-unified AutoScan ranking evidence.

    Older AutoScan/scan-hot snapshots used TOP position to manufacture a score.
    Remove those snapshots and clear every *live* aggregate field they could have
    contaminated. Product ids/favorites/associations remain as catalogue history.
    Peak Score is recomputed only from surviving non-synthetic snapshots.
    """
    stats = {
        "snapshots_removed": 0, "products_reset": 0,
        "active_lifecycle_removed": 0, "peaks_recomputed": 0,
    }
    async with SessionLocal() as session:
        done = await session.get(AppSetting, RADAR_UNIFIED_48H_REPAIR_SETTING)
        if done is not None and str(done.value or "").strip() == "1":
            return stats
        deleted = await session.execute(delete(RadarSnapshot).where(
            RadarSnapshot.source.in_(["radar_autoscan", "scan_hot"])
        ))
        stats["snapshots_removed"] = int(deleted.rowcount or 0)
        active_watches = await session.execute(delete(RadarLifecycleWatch).where(
            RadarLifecycleWatch.status.in_(["watching", "confirming"])
        ))
        stats["active_lifecycle_removed"] = int(active_watches.rowcount or 0)

        surviving = (await session.execute(
            select(
                RadarSnapshot.product_id,
                func.max(RadarSnapshot.score),
                func.count(RadarSnapshot.id),
            ).group_by(RadarSnapshot.product_id)
        )).all()
        surviving_by_product = {
            int(product_id): (int(peak or 0), int(count or 0))
            for product_id, peak, count in surviving if product_id is not None
        }
        products = list((await session.execute(select(RadarProduct))).scalars().all())
        for product in products:
            peak, signal_count = surviving_by_product.get(int(product.id), (0, 0))
            product.peak_score = _clamp_score(peak)
            product.signal_count = signal_count
            product.last_signal_score = 0
            product.current_score = 0
            product.confidence = 0
            product.radar_rank = 0.0
            product.demand_views = 0
            product.demand_age_minutes = 0.0
            product.demand_gate = 0
            product.status = "historical"
            product.latest_reason = ""
            product.latest_source = ""
            product.updated_at = datetime.utcnow()
            stats["products_reset"] += 1
            if signal_count:
                stats["peaks_recomputed"] += 1

        now = datetime.utcnow()
        if done is None:
            session.add(AppSetting(key=RADAR_UNIFIED_48H_REPAIR_SETTING, value="1", updated_at=now))
        else:
            done.value = "1"
            done.updated_at = now
        await session.commit()
    if any(stats.values()):
        log.warning("v4.20.0 Unified 48H ranking repair: %s", stats)
    return stats


async def prepare_verified_organic_velocity_once() -> dict[str, int]:
    """Remove pre-v4.15.7 400+ inherited totals from analytical influence.

    v4.15.6 allowed a same-day listing to use its total immediately.  v4.15.7
    changes that contract: if DT first saw 400+ views, the total becomes an
    untrusted baseline and two *new* clean checkpoints are required.  This startup
    repair is intentionally conservative and does not label those ads promoted.
    """
    stats = {
        "listings_reset": 0, "ai_candidates": 0, "lifecycle_watches": 0,
        "radar_products_quarantined": 0,
    }
    async with SessionLocal() as session:
        done = await session.get(AppSetting, RADAR_VELOCITY_PREP_SETTING)
        if done is not None and str(done.value or "").strip() == "1":
            return stats

        rows = list((await session.execute(
            select(Listing).where(
                Listing.organic_baseline_views.is_not(None),
                Listing.organic_baseline_views >= int(ORGANIC_HIGH_BASELINE_VIEWS),
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
                ~_registry_dirty_exists(Listing.external_id),
            )
        )).scalars().all())
        ids = sorted({str(row.external_id) for row in rows if str(row.external_id or "")})
        for row in rows:
            row.organic_history_status = "high_baseline"
            row.organic_verified_checkpoints = 0
            row.organic_last_checkpoint_at = row.organic_baseline_at
            row.organic_last_checkpoint_views = row.organic_baseline_views
        stats["listings_reset"] = len(ids)

        if ids:
            candidate_rows = (await session.execute(
                select(AIEarlyWinnerCandidate.id, AIEarlyWinnerCandidate.run_id).where(
                    AIEarlyWinnerCandidate.external_id.in_(ids)
                )
            )).all()
            candidate_ids = sorted({int(row[0]) for row in candidate_rows})
            run_ids = sorted({int(row[1]) for row in candidate_rows})
            if candidate_ids:
                await session.execute(delete(AIEarlyWinnerObservation).where(
                    AIEarlyWinnerObservation.candidate_id.in_(candidate_ids)
                ))
                await session.execute(delete(AIEarlyWinnerEvent).where(
                    AIEarlyWinnerEvent.candidate_id.in_(candidate_ids)
                ))
                deleted = await session.execute(delete(AIEarlyWinnerCandidate).where(
                    AIEarlyWinnerCandidate.id.in_(candidate_ids)
                ))
                stats["ai_candidates"] = int(deleted.rowcount or 0)

            affected_product_ids = set((await session.execute(
                select(RadarSnapshot.product_id).where(RadarSnapshot.external_id.in_(ids))
            )).scalars().all())
            affected_product_ids.update((await session.execute(
                select(RadarProductListing.product_id).where(RadarProductListing.external_id.in_(ids))
            )).scalars().all())
            affected_product_ids.update((await session.execute(
                select(RadarLifecycleWatch.product_id).where(RadarLifecycleWatch.external_id.in_(ids))
            )).scalars().all())
            if affected_product_ids:
                result = await session.execute(
                    update(RadarProduct)
                    .where(RadarProduct.id.in_(sorted(int(x) for x in affected_product_ids if x is not None)))
                    .values(organic_verified_at=None)
                )
                stats["radar_products_quarantined"] = int(result.rowcount or 0)
            deleted_watches = await session.execute(delete(RadarLifecycleWatch).where(
                RadarLifecycleWatch.external_id.in_(ids)
            ))
            stats["lifecycle_watches"] = int(deleted_watches.rowcount or 0)

            # Keep AI run counters truthful after removing candidates whose old
            # initial scores were based on inherited 400+ totals.
            for run_id in run_ids:
                run = await session.get(AIEarlyWinnerRun, int(run_id))
                if run is None:
                    continue
                remaining = list((await session.execute(
                    select(AIEarlyWinnerCandidate.is_control).where(
                        AIEarlyWinnerCandidate.run_id == int(run_id)
                    )
                )).scalars().all())
                run.candidate_count = sum(1 for x in remaining if not bool(x))
                run.control_count = sum(1 for x in remaining if bool(x))

        if done is None:
            session.add(AppSetting(key=RADAR_VELOCITY_PREP_SETTING, value="1"))
        else:
            done.value = "1"
        await session.commit()

    if any(stats.values()):
        log.warning(
            "v4.15.7 Verified Organic Velocity repair: listings=%s ai=%s lifecycle=%s radar_quarantined=%s threshold=%s",
            stats["listings_reset"], stats["ai_candidates"], stats["lifecycle_watches"],
            stats["radar_products_quarantined"], ORGANIC_HIGH_BASELINE_VIEWS,
        )
    return stats


async def prepare_bump_resurrection_sweep_once() -> bool:
    """Quarantine pre-v4.15.6 Radar once so polluted families cannot flash during sweep."""
    async with SessionLocal() as session:
        done = await session.get(AppSetting, RADAR_BUMP_SWEEP_SETTING)
        if done is not None and str(done.value or "").strip() == "1":
            return False
        applied = await session.get(AppSetting, RADAR_BUMP_QUARANTINE_SETTING)
        if applied is None or str(applied.value or "").strip() != "1":
            await session.execute(update(RadarProduct).values(organic_verified_at=None))
            now = datetime.utcnow()
            if applied is None:
                session.add(AppSetting(key=RADAR_BUMP_QUARANTINE_SETTING, value="1", updated_at=now))
            else:
                applied.value = "1"
                applied.updated_at = now
            await session.commit()
            log.warning("v4.15.6 quarantined existing Radar pending bump-resurrection integrity sweep")
        return True


async def bump_resurrection_integrity_sweep_once() -> dict[str, int]:
    """Re-verify every current Radar association with v4.15.6 bump semantics.

    Dirty ads are stickily marked and purged through the normal cleanup path. Clean
    legacy families are only marked sweep-verified and stay user-hidden until a fresh
    v4.15.6 demand-safe signal rebuilds/certifies them. UNKNOWN remains quarantined
    and is retried on the next maintenance cycle/restart rather than guessed organic.
    """
    needed = await prepare_bump_resurrection_sweep_once()
    if not needed:
        return {"products": 0, "checked": 0, "clean": 0, "dirty": 0, "unknown": 0, "sweep_verified": 0}

    stats = {"products": 0, "checked": 0, "clean": 0, "dirty": 0, "unknown": 0, "sweep_verified": 0}
    interrupted = False
    async with SessionLocal() as session:
        product_ids = list((await session.execute(
            select(RadarProduct.id)
            .where(RadarProduct.bump_sweep_verified_at.is_(None))
            .order_by(RadarProduct.id.asc())
        )).scalars().all())

    for product_id in product_ids:
        traffic_snapshot = await TRAFFIC.snapshot()
        if int(getattr(traffic_snapshot, "scan_jobs_active", 0) or 0) > 0 or int(getattr(traffic_snapshot, "background_pauses", 0) or 0) > 0:
            interrupted = True
            log.info("v4.20.0 Radar sweep paused for foreground scan checked=%s", stats["checked"])
            break
        stats["products"] += 1
        async with SessionLocal() as session:
            pairs = (await session.execute(
                select(Listing, RadarProductListing)
                .join(RadarProductListing, RadarProductListing.external_id == Listing.external_id)
                .where(RadarProductListing.product_id == int(product_id))
                .order_by(RadarProductListing.last_seen_at.desc())
            )).all()
        if not pairs:
            continue
        product_clean = True
        any_clean = False
        for listing, _assoc in pairs:
            traffic_snapshot = await TRAFFIC.snapshot()
            if int(getattr(traffic_snapshot, "scan_jobs_active", 0) or 0) > 0 or int(getattr(traffic_snapshot, "background_pauses", 0) or 0) > 0:
                interrupted = True
                product_clean = False
                log.info("v4.20.0 Radar sweep yielded inside family product=%s checked=%s", product_id, stats["checked"])
                break
            stats["checked"] += 1
            allowed, reason, _verified_at = await _live_detail_organic_gate(
                listing, force_priority="background"
            )
            if allowed:
                any_clean = True
                stats["clean"] += 1
                continue
            if "promoted" in reason or "reduced" in reason:
                stats["dirty"] += 1
                # purge may remove this association/product; continue checking the
                # detached list only for telemetry, then re-query before certification.
                continue
            product_clean = False
            stats["unknown"] += 1
            log.info(
                "v4.15.6 Radar sweep kept family quarantined product=%s external_id=%s reason=%s",
                product_id, listing.external_id, reason,
            )
        if product_clean and any_clean:
            async with SessionLocal() as session:
                product = await session.get(RadarProduct, int(product_id))
                if product is not None:
                    remaining = int((await session.execute(
                        select(func.count(RadarProductListing.id)).where(RadarProductListing.product_id == int(product_id))
                    )).scalar_one() or 0)
                    if remaining > 0:
                        # Historical detail cleanliness is not enough to trust old
                        # accumulated view totals. Mark the one-time sweep complete for
                        # this family, but keep organic_verified_at NULL. A fresh strict
                        # v4.15.6 signal will reset legacy snapshots and certify it.
                        product.bump_sweep_verified_at = datetime.utcnow()
                        product.updated_at = datetime.utcnow()
                        await session.commit()
                        stats["sweep_verified"] += 1
        if stats["checked"] and stats["checked"] % 25 == 0:
            await asyncio.sleep(0.25)

    if not interrupted and stats["unknown"] == 0:
        async with SessionLocal() as session:
            setting = await session.get(AppSetting, RADAR_BUMP_SWEEP_SETTING)
            now = datetime.utcnow()
            if setting is None:
                session.add(AppSetting(key=RADAR_BUMP_SWEEP_SETTING, value="1", updated_at=now))
            else:
                setting.value = "1"
                setting.updated_at = now
            await session.commit()
    log.warning("v4.15.6 bump-resurrection Radar sweep: %s", stats)
    return stats


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
