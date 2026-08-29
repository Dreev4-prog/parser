from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from collections import defaultdict
from datetime import datetime, timedelta

# AI Worker never opens its own Kleinanzeigen browser. It delegates the tiny
# candidate checkpoints to the existing exact View Worker fleet.
os.environ.setdefault("REMOTE_VIEW_WORKER_ENABLED", "1")
# Keep the AI service on the small distributed PostgreSQL pool even when the
# Railway project does not explicitly copy DISTRIBUTED_WORKERS to this service.
os.environ.setdefault("DISTRIBUTED_WORKERS", "1")

from sqlalchemy import exists, func, or_, select, update

from ai_manager import AI_HEARTBEAT_KEY
from app_version import APP_VERSION
from db import SessionLocal, init_db
from early_winner import (
    MODEL_VERSION,
    FeatureRow,
    listing_age_minutes,
    opportunity_family_key,
    score_initial_rows,
    select_candidates,
    update_dynamic_score,
)
from organic_velocity import demand_safe_metric
from models import (
    AIEarlyWinnerCandidate,
    AIEarlyWinnerEvent,
    AIEarlyWinnerObservation,
    AIEarlyWinnerRun,
    AppSetting,
    Listing,
    ScanListing,
    UserScan,
    ViewHistory,
)
from view_manager import REMOTE_VIEW_MANAGER
from radar import record_ai_candidate

try:
    from redis.asyncio import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("dtparser-ai-worker")

REDIS_URL = os.getenv("REDIS_URL", "").strip()
AI_ENABLED = os.getenv("AI_EARLY_WINNER_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
AI_POLL_SECONDS = max(3.0, min(60.0, float(os.getenv("AI_POLL_SECONDS", "8"))))
AI_INITIAL_BACKFILL_MINUTES = max(0, min(240, int(os.getenv("AI_INITIAL_BACKFILL_MINUTES", "30"))))
AI_MAX_AGE_HOURS = max(3.0, min(48.0, float(os.getenv("AI_EARLY_MAX_AGE_HOURS", "24"))))
AI_SCORE_FLOOR = max(40, min(90, int(os.getenv("AI_EARLY_SCORE_FLOOR", "65"))))
AI_CANDIDATES_PER_CATEGORY = max(2, min(20, int(os.getenv("AI_CANDIDATES_PER_CATEGORY", "10"))))
AI_TOTAL_CANDIDATES = max(5, min(50, int(os.getenv("AI_TOTAL_CANDIDATES", "20"))))
AI_MAX_PER_COHORT = max(1, min(5, int(os.getenv("AI_MAX_PER_COHORT", "2"))))
AI_REPEAT_SUPPRESS_HOURS = max(1, min(72, int(os.getenv("AI_REPEAT_SUPPRESS_HOURS", "12"))))
AI_MARKET_SAMPLE_LIMIT = max(5000, min(60000, int(os.getenv("AI_MARKET_SAMPLE_LIMIT", "30000"))))
AI_CONTROL_PER_CATEGORY = max(0, min(5, int(os.getenv("AI_CONTROL_PER_CATEGORY", "2"))))
AI_MARKET_LOOKBACK_DAYS = max(7, min(180, int(os.getenv("AI_MARKET_LOOKBACK_DAYS", "30"))))
AI_TREND_WINDOW_DAYS = max(3, min(21, int(os.getenv("AI_TREND_WINDOW_DAYS", "7"))))
AI_OBSERVATION_BATCH = max(1, min(50, int(os.getenv("AI_OBSERVATION_BATCH", "24"))))
AI_REUSE_WINDOW_MINUTES = max(3, min(45, int(os.getenv("AI_REUSE_WINDOW_MINUTES", "15"))))
AI_OBSERVATION_LATE_GRACE_MINUTES = max(30, min(240, int(os.getenv("AI_OBSERVATION_LATE_GRACE_MINUTES", "90"))))
AI_RETRY_MINUTES = max(5, min(60, int(os.getenv("AI_RETRY_MINUTES", "15"))))
AI_MAX_ATTEMPTS = max(1, min(6, int(os.getenv("AI_MAX_ATTEMPTS", "3"))))
AI_PAUSE_DURING_USER_SCANS = os.getenv("AI_PAUSE_DURING_USER_SCANS", "1").strip().lower() not in {"0", "false", "no", "off"}
AI_CHECKPOINT_HOURS = tuple(
    sorted({int(x) for x in os.getenv("AI_CHECKPOINT_HOURS", "1,3,6").split(",") if x.strip().isdigit() and 1 <= int(x) <= 12})
) or (1, 3, 6)
START_SETTING_KEY = "ai_early_winner_shadow_started_at"


class AIWorker:
    def __init__(self) -> None:
        if not REDIS_URL:
            raise RuntimeError("AI Worker requires REDIS_URL")
        if Redis is None:
            raise RuntimeError("redis package is not installed")
        self.redis = Redis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=20,
        )
        self.consumer = f"ai-{socket.gethostname()}-{os.getpid()}"
        self.started_at = time.monotonic()
        self.analyzed_runs = 0
        self.created_candidates = 0
        self.observations_done = 0
        self.observations_reused = 0
        self.observations_remote = 0
        self.last_error = ""
        self.paused_for_scans = False
        self._last_hb = 0.0
        self.shadow_started_at = datetime.utcnow()

    async def setup(self) -> None:
        # Main Bot and AI Worker can deploy at the same time on Railway. Both may
        # legitimately be the first process to create the new additive AI tables,
        # so retry startup briefly if PostgreSQL DDL is momentarily racing.
        last_db_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                await init_db()
                last_db_error = None
                break
            except Exception as exc:
                last_db_error = exc
                if attempt >= 3:
                    raise
                log.warning("AI DB initialization retry %s/3: %s", attempt, exc)
                await asyncio.sleep(2.0 * attempt)
        if last_db_error is not None:
            raise last_db_error
        await self.redis.ping()
        self.shadow_started_at = await self._ensure_shadow_start()
        await self._recover_stale_work()
        log.info(
            "DT AI Worker ready version=%s model=%s shadow_start=%s checkpoints=%s max_age=%sh",
            APP_VERSION, MODEL_VERSION, self.shadow_started_at.isoformat(), AI_CHECKPOINT_HOURS, AI_MAX_AGE_HOURS,
        )

    async def _ensure_shadow_start(self) -> datetime:
        async with SessionLocal() as session:
            setting = await session.get(AppSetting, START_SETTING_KEY)
            if setting is not None and setting.value:
                try:
                    return datetime.fromisoformat(setting.value)
                except ValueError:
                    pass
            now = datetime.utcnow()
            shadow_start = now - timedelta(minutes=AI_INITIAL_BACKFILL_MINUTES)
            if setting is None:
                setting = AppSetting(key=START_SETTING_KEY, value=shadow_start.isoformat(), updated_at=now)
                session.add(setting)
            else:
                setting.value = shadow_start.isoformat()
                setting.updated_at = now
            await session.commit()
            return shadow_start

    async def _recover_stale_work(self) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=20)
        async with SessionLocal() as session:
            runs = (await session.execute(select(AIEarlyWinnerRun).where(
                AIEarlyWinnerRun.status == "running",
                AIEarlyWinnerRun.started_at.is_not(None),
                AIEarlyWinnerRun.started_at < cutoff,
            ))).scalars().all()
            for run in runs:
                run.status = "pending"
                run.started_at = None
            observations = (await session.execute(select(AIEarlyWinnerObservation).where(
                AIEarlyWinnerObservation.status == "running",
                AIEarlyWinnerObservation.started_at.is_not(None),
                AIEarlyWinnerObservation.started_at < cutoff,
            ))).scalars().all()
            for obs in observations:
                obs.status = "pending"
                obs.started_at = None
            await session.commit()

    async def heartbeat(self) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_hb < 3.0:
            return
        self._last_hb = now_mono
        payload = {
            "ts": time.time(),
            "version": APP_VERSION,
            "model_version": MODEL_VERSION,
            "consumer": self.consumer,
            "uptime_seconds": int(now_mono - self.started_at),
            "analyzed_runs": self.analyzed_runs,
            "created_candidates": self.created_candidates,
            "observations_done": self.observations_done,
            "observations_reused": self.observations_reused,
            "observations_remote": self.observations_remote,
            "paused_for_scans": self.paused_for_scans,
            "last_error": self.last_error[:300],
        }
        await self.redis.set(AI_HEARTBEAT_KEY, json.dumps(payload, ensure_ascii=False), ex=20)

    async def active_user_scans(self) -> int:
        async with SessionLocal() as session:
            count = (await session.execute(select(func.count(UserScan.id)).where(
                UserScan.status.in_(["queued", "running", "cancelling"]),
                UserScan.finished_at.is_(None),
            ))).scalar_one()
            return int(count or 0)

    async def find_scan_to_analyze(self) -> UserScan | None:
        """Return the oldest completed scan that has no AI run yet.

        v4.5.0 first fetched 20 scans and only then checked which were analyzed.
        Once those first 20 were done, scan #21 could starve forever. The NOT EXISTS
        filter is now executed by PostgreSQL, so the worker keeps advancing.
        """
        async with SessionLocal() as session:
            scan = (await session.execute(
                select(UserScan)
                .where(
                    UserScan.status == "done",
                    UserScan.target_complete.is_(True),
                    UserScan.finished_at.is_not(None),
                    UserScan.finished_at >= self.shadow_started_at,
                    UserScan.result_count > 0,
                    ~exists().where(AIEarlyWinnerRun.scan_id == UserScan.id),
                )
                .order_by(UserScan.finished_at.asc())
                .limit(1)
            )).scalar_one_or_none()
            if scan is not None:
                session.expunge(scan)
            return scan

    async def _market_stats(self, features: list[FeatureRow]) -> dict[str, dict]:
        """Build category-relative supply + demand trend profiles from our own DB.

        v4.5.1 treated a fixed number of listings as "mass market" in every category.
        v4.6 instead calculates saturation percentile *inside the category*, recent vs
        previous supply growth, and observed ViewHistory momentum when enough points exist.
        Missing history stays neutral; it never receives a rarity bonus.
        """
        if not features:
            return {}
        target_keys: set[str] = set()
        categories = {str(x.category_key or "unknown") for x in features}
        for feature in features:
            if feature.identity_key and int(feature.identity_confidence or 0) >= 70:
                target_keys.add(f"id:{feature.identity_key}")
            else:
                key = feature.family_key or opportunity_family_key(feature.title, feature.category_key)
                if key:
                    target_keys.add(key)
        if not target_keys:
            return {}

        now = datetime.utcnow()
        cutoff = now - timedelta(days=AI_MARKET_LOOKBACK_DAYS)
        recent_cutoff = now - timedelta(days=AI_TREND_WINDOW_DAYS)
        previous_cutoff = now - timedelta(days=AI_TREND_WINDOW_DAYS * 2)

        def family_key(category_key, identity_key, identity_confidence, title) -> str:
            if identity_key and int(identity_confidence or 0) >= 70:
                return f"id:{identity_key}"
            return opportunity_family_key(str(title or ""), str(category_key or "unknown"))

        async with SessionLocal() as session:
            listing_rows = (await session.execute(
                select(
                    Listing.category_key, Listing.identity_key, Listing.identity_confidence,
                    Listing.title, Listing.price_eur, Listing.first_seen_at,
                )
                .where(
                    Listing.category_key.in_(sorted(categories)),
                    Listing.first_seen_at >= cutoff,
                    Listing.is_promoted.is_(False),
                    Listing.is_price_reduced.is_(False),
                )
                .order_by(Listing.first_seen_at.desc())
                .limit(AI_MARKET_SAMPLE_LIMIT)
            )).all()

            # Prior AI results add repeatability evidence across independent scans.
            prior_rows = (await session.execute(
                select(
                    AIEarlyWinnerCandidate.cohort_key,
                    AIEarlyWinnerCandidate.outcome,
                    AIEarlyWinnerCandidate.current_score,
                )
                .join(Listing, Listing.external_id == AIEarlyWinnerCandidate.external_id)
                .where(
                    AIEarlyWinnerCandidate.created_at >= cutoff,
                    AIEarlyWinnerCandidate.is_control.is_(False),
                    AIEarlyWinnerCandidate.cohort_key.in_(sorted(target_keys)),
                    Listing.is_promoted.is_(False),
                    Listing.is_price_reduced.is_(False),
                ).limit(AI_MARKET_SAMPLE_LIMIT)
            )).all()

            async def view_rows_for_period(start_at: datetime, end_at: datetime | None):
                conditions = [
                    Listing.category_key.in_(sorted(categories)),
                    Listing.first_seen_at >= start_at,
                    ViewHistory.recorded_at >= start_at,
                    Listing.is_promoted.is_(False),
                    Listing.is_price_reduced.is_(False),
                    Listing.organic_history_status.in_(["trusted", "trusted_new", "observed"]),
                ]
                if end_at is not None:
                    conditions.extend([Listing.first_seen_at < end_at, ViewHistory.recorded_at < end_at + timedelta(days=2)])
                return (await session.execute(
                    select(
                        ViewHistory.external_id, ViewHistory.view_count, ViewHistory.recorded_at,
                        Listing.category_key, Listing.identity_key, Listing.identity_confidence,
                        Listing.title, Listing.first_seen_at,
                    )
                    .join(Listing, Listing.external_id == ViewHistory.external_id)
                    .where(*conditions)
                    .order_by(ViewHistory.recorded_at.asc())
                    .limit(AI_MARKET_SAMPLE_LIMIT)
                )).all()

            recent_view_rows = await view_rows_for_period(recent_cutoff, None)
            previous_view_rows = await view_rows_for_period(previous_cutoff, recent_cutoff)

        counts: dict[str, int] = defaultdict(int)
        prices_by_key: dict[str, list[int]] = defaultdict(list)
        recent_counts: dict[str, int] = defaultdict(int)
        previous_counts: dict[str, int] = defaultdict(int)
        category_family_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        category_recent_total: dict[str, int] = defaultdict(int)
        category_previous_total: dict[str, int] = defaultdict(int)

        for category_key, identity_key, identity_confidence, title, price, first_seen_at in listing_rows:
            cat = str(category_key or "unknown")
            key = family_key(cat, identity_key, identity_confidence, title)
            if not key:
                continue
            counts[key] += 1
            category_family_counts[cat][key] += 1
            if price is not None and int(price) > 0:
                prices_by_key[key].append(int(price))
            seen = first_seen_at or cutoff
            if seen >= recent_cutoff:
                recent_counts[key] += 1
                category_recent_total[cat] += 1
            elif seen >= previous_cutoff:
                previous_counts[key] += 1
                category_previous_total[cat] += 1

        def pct_rank(value: int, values: list[int]) -> float:
            vals = sorted(int(v) for v in values if int(v) >= 0)
            if not vals:
                return 0.0
            below = sum(1 for v in vals if v < value)
            equal = sum(1 for v in vals if v == value)
            return max(0.0, min(1.0, (below + 0.5 * equal) / len(vals)))

        # Demand rate from two or more actual ViewHistory checkpoints for one listing.
        # Build it for the whole selected category set, not only target families: the
        # surrounding category is required for raw persistence/repeatability baselines.
        def demand_rates(rows) -> tuple[dict[str, list[float]], dict[str, str]]:
            points: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
            category_by_key: dict[str, str] = {}
            for external_id, view_count, recorded_at, category_key, identity_key, identity_confidence, title, _first_seen in rows:
                key = family_key(category_key, identity_key, identity_confidence, title)
                if not key or recorded_at is None:
                    continue
                category_by_key[key] = str(category_key or "unknown")
                points[(key, str(external_id))].append((recorded_at, int(view_count or 0)))
            rates: dict[str, list[float]] = defaultdict(list)
            for (key, _external_id), series in points.items():
                if len(series) < 2:
                    continue
                series.sort(key=lambda x: x[0])
                first_at, first_views = series[0]
                last_at, last_views = series[-1]
                hours = (last_at - first_at).total_seconds() / 3600.0
                if hours < 0.50 or last_views < first_views:
                    continue
                rates[key].append((last_views - first_views) / hours)
            return rates, category_by_key

        recent_demand, recent_key_categories = demand_rates(recent_view_rows)
        previous_demand, previous_key_categories = demand_rates(previous_view_rows)

        category_recent_demand: dict[str, list[float]] = defaultdict(list)
        category_previous_demand: dict[str, list[float]] = defaultdict(list)
        for key, values in recent_demand.items():
            category_recent_demand[recent_key_categories.get(key, "unknown")].extend(values)
        for key, values in previous_demand.items():
            category_previous_demand[previous_key_categories.get(key, "unknown")].extend(values)
        prior_signals: dict[str, int] = defaultdict(int)
        prior_confirmed: dict[str, int] = defaultdict(int)
        for key, outcome, current_score in prior_rows:
            key = str(key or "")
            if not key:
                continue
            if int(current_score or 0) >= 65:
                prior_signals[key] += 1
            if str(outcome or "") == "confirmed":
                prior_confirmed[key] += 1

        def median(values: list[float]) -> float | None:
            vals = sorted(float(x) for x in values if x is not None)
            if not vals:
                return None
            n = len(vals)
            mid = n // 2
            return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0

        def percentile_rank_float(value: float, values: list[float]) -> float:
            vals = sorted(float(x) for x in values if x is not None)
            if not vals:
                return 0.5
            if len(vals) == 1:
                return 0.5
            below = sum(1 for x in vals if x < float(value))
            equal = sum(1 for x in vals if x == float(value))
            return max(0.0, min(1.0, (below + 0.5 * equal) / len(vals)))

        def historical_demand_quality(key: str, cat: str) -> tuple[float, float]:
            """Return (persistence, repeatability) from raw ViewHistory only.

            Persistence asks whether the family stays above its category across
            independent historical windows. Repeatability asks how often individual
            listings land in the category's upper-demand quartile. Both shrink toward
            neutral 0.5 when evidence is thin.
            """
            recent_rates = list(recent_demand.get(key, []))
            previous_rates = list(previous_demand.get(key, []))
            recent_category = list(category_recent_demand.get(cat, []))
            previous_category = list(category_previous_demand.get(cat, []))

            period_scores: list[float] = []
            if recent_rates and recent_category:
                recent_period = median([percentile_rank_float(v, recent_category) for v in recent_rates])
                period_scores.append(0.5 if recent_period is None else recent_period)
            if previous_rates and previous_category:
                previous_period = median([percentile_rank_float(v, previous_category) for v in previous_rates])
                period_scores.append(0.5 if previous_period is None else previous_period)

            total_samples = len(recent_rates) + len(previous_rates)
            if not period_scores:
                persistence = 0.5
            elif len(period_scores) == 1:
                # One historical window is useful but not enough to call the signal
                # persistent; shrink it toward neutral.
                persistence = 0.65 * period_scores[0] + 0.35 * 0.5
            else:
                floor = min(period_scores)
                mean_score = sum(period_scores) / len(period_scores)
                persistence = 0.65 * floor + 0.35 * mean_score
            depth = min(1.0, total_samples / 8.0)
            persistence = max(0.0, min(1.0, 0.5 + (persistence - 0.5) * (0.45 + 0.55 * depth)))

            success = 0
            trials = 0
            if recent_category:
                for rate in recent_rates:
                    trials += 1
                    if percentile_rank_float(rate, recent_category) >= 0.75:
                        success += 1
            if previous_category:
                for rate in previous_rates:
                    trials += 1
                    if percentile_rank_float(rate, previous_category) >= 0.75:
                        success += 1
            # Beta(2,2) prior: raw history cannot jump to 0 or 1 from one listing.
            repeatability = (success + 2.0) / (trials + 4.0) if trials else 0.5
            return (
                max(0.0, min(1.0, float(persistence))),
                max(0.0, min(1.0, float(repeatability))),
            )

        stats: dict[str, dict] = {}
        feature_cat_by_key: dict[str, str] = {}
        for feature in features:
            key = f"id:{feature.identity_key}" if feature.identity_key and int(feature.identity_confidence or 0) >= 70 else (feature.family_key or opportunity_family_key(feature.title, feature.category_key))
            if key:
                feature_cat_by_key[key] = str(feature.category_key or "unknown")

        for key in target_keys:
            count = int(counts.get(key, 0))
            prices = sorted(prices_by_key.get(key, []))
            price_median = median([float(x) for x in prices])
            cat = feature_cat_by_key.get(key, "unknown")
            family_counts = list(category_family_counts.get(cat, {}).values())
            supply_percentile = pct_rank(count, family_counts) if count > 0 else 0.0

            recent = int(recent_counts.get(key, 0))
            previous = int(previous_counts.get(key, 0))
            cohort_growth = (recent + 1.0) / (previous + 1.0)
            category_growth = (category_recent_total.get(cat, 0) + 2.0) / (category_previous_total.get(cat, 0) + 2.0)
            supply_growth_ratio = max(0.25, min(4.0, cohort_growth / max(0.25, category_growth)))

            recent_rates = recent_demand.get(key, [])
            previous_rates = previous_demand.get(key, [])
            recent_median = median(recent_rates)
            previous_median = median(previous_rates)
            demand_growth_ratio = 1.0
            if recent_median is not None and previous_median is not None and previous_median > 0:
                demand_growth_ratio = max(0.25, min(4.0, recent_median / previous_median))
            persistence, historical_repeatability = historical_demand_quality(key, cat)

            stats[key] = {
                "median": price_median,
                "count": count,
                "supply_percentile": supply_percentile,
                "recent_count": recent,
                "previous_count": previous,
                "supply_growth_ratio": supply_growth_ratio,
                "demand_recent_median": recent_median,
                "demand_previous_median": previous_median,
                "demand_growth_ratio": demand_growth_ratio,
                "demand_recent_samples": len(recent_rates),
                "demand_previous_samples": len(previous_rates),
                "persistence": persistence,
                "historical_repeatability": historical_repeatability,
                "prior_signals": int(prior_signals.get(key, 0)),
                "prior_confirmed": int(prior_confirmed.get(key, 0)),
            }
        return stats

    async def analyze_scan(self, scan: UserScan) -> None:
        now = datetime.utcnow()
        run: AIEarlyWinnerRun | None = None
        try:
            async with SessionLocal() as session:
                existing = (await session.execute(select(AIEarlyWinnerRun).where(
                    AIEarlyWinnerRun.scan_id == int(scan.id)
                ))).scalar_one_or_none()
                if existing is not None:
                    return
                run = AIEarlyWinnerRun(
                    scan_id=int(scan.id), status="running", model_version=MODEL_VERSION,
                    created_at=now, started_at=now,
                )
                session.add(run)
                await session.commit()
                await session.refresh(run)

            async with SessionLocal() as session:
                pairs = (await session.execute(
                    select(Listing, ScanListing)
                    .join(ScanListing, Listing.external_id == ScanListing.external_id)
                    .where(
                        ScanListing.scan_id == int(scan.id),
                        Listing.is_promoted.is_(False),
                        Listing.is_price_reduced.is_(False),
                    )
                )).all()

            features: list[FeatureRow] = []
            listing_by_id: dict[str, Listing] = {}
            snapshot_by_id: dict[str, ScanListing] = {}
            category_by_id: dict[str, str] = {}
            for listing, snap in pairs:
                listing_by_id[listing.external_id] = listing
                snapshot_by_id[listing.external_id] = snap
                if snap.initial_view_count is None:
                    continue
                metric = demand_safe_metric(listing, int(snap.initial_view_count), snap.captured_at)
                if metric.views is None:
                    # v4.15.7: a first-seen 400+ counter is baseline-only.  It
                    # cannot enter DT Demand Score until two later clean exact
                    # checkpoints certify the post-baseline delta.
                    continue
                effective_views = int(metric.views)
                if metric.kind == "observed_delta":
                    age_minutes = metric.age_minutes
                    exact_clock = age_minutes is not None
                else:
                    age_minutes, exact_clock = listing_age_minutes(listing.posted_text, snap.captured_at)
                if not exact_clock or age_minutes is None:
                    continue
                if age_minutes < 5.0 or age_minutes > AI_MAX_AGE_HOURS * 60.0:
                    continue
                category_key = str(listing.category_key or "unknown")
                category_by_id[listing.external_id] = category_key
                features.append(FeatureRow(
                    external_id=listing.external_id,
                    category_key=category_key,
                    identity_key=listing.identity_key,
                    identity_label=listing.identity_label,
                    identity_confidence=listing.identity_confidence,
                    price_eur=listing.price_eur,
                    views=int(effective_views),
                    age_minutes=float(age_minutes),
                    title=str(listing.title or ""),
                    family_key=opportunity_family_key(str(listing.title or ""), category_key),
                ))

            market_stats = await self._market_stats(features)
            scores = score_initial_rows(features, market_stats)
            score_by_id = {x.external_id: x for x in scores}
            selected_ids, controls = select_candidates(
                scores,
                category_by_id,
                score_floor=AI_SCORE_FLOOR,
                per_category=AI_CANDIDATES_PER_CATEGORY,
                total_limit=AI_TOTAL_CANDIDATES,
                control_per_category=AI_CONTROL_PER_CATEGORY,
                max_per_cohort=AI_MAX_PER_COHORT,
            )

            # The same live listing can appear in several users' scans. Do not create
            # parallel +1/+3/+6 observation plans for it every time.
            if selected_ids:
                repeat_cutoff = datetime.utcnow() - timedelta(hours=AI_REPEAT_SUPPRESS_HOURS)
                async with SessionLocal() as session:
                    repeated = set((await session.execute(
                        select(AIEarlyWinnerCandidate.external_id).where(
                            AIEarlyWinnerCandidate.external_id.in_(selected_ids),
                            AIEarlyWinnerCandidate.created_at >= repeat_cutoff,
                            AIEarlyWinnerCandidate.is_control.is_(False),
                        )
                    )).scalars().all())
                if repeated:
                    selected_ids = [x for x in selected_ids if x not in repeated]
                    controls.difference_update(repeated)

            created_candidate_ids: list[int] = []
            async with SessionLocal() as session:
                db_run = await session.get(AIEarlyWinnerRun, int(run.id))
                if db_run is None:
                    return
                db_run.listing_count = len(pairs)
                db_run.eligible_count = len(features)
                db_run.candidate_count = sum(1 for x in selected_ids if x not in controls)
                db_run.control_count = sum(1 for x in selected_ids if x in controls)

                for external_id in selected_ids:
                    score = score_by_id.get(external_id)
                    listing = listing_by_id.get(external_id)
                    snap = snapshot_by_id.get(external_id)
                    if score is None or listing is None or snap is None or snap.initial_view_count is None:
                        continue
                    candidate = AIEarlyWinnerCandidate(
                        run_id=int(db_run.id),
                        scan_id=int(scan.id),
                        external_id=external_id,
                        category_key=str(listing.category_key or ""),
                        identity_key=listing.identity_key,
                        cohort_key=score.cohort_key,
                        opportunity_type=score.opportunity_type,
                        saturation_score=int(score.saturation_score),
                        supply_percentile=float(score.supply_percentile),
                        supply_growth_ratio=float(score.supply_growth_ratio),
                        demand_growth_ratio=float(score.demand_growth_ratio),
                        demand_supply_ratio=float(score.demand_supply_ratio),
                        repeatability=float(score.repeatability),
                        is_control=external_id in controls,
                        baseline_at=snap.captured_at,
                        baseline_views=int(snap.initial_view_count),
                        age_minutes=float(next(x.age_minutes for x in features if x.external_id == external_id)),
                        initial_views_per_hour=float(score.views_per_hour),
                        latest_views=int(snap.initial_view_count),
                        latest_at=snap.captured_at,
                        initial_score=int(score.score),
                        current_score=int(score.score),
                        confidence=int(score.confidence),
                        stage=score.stage,
                        outcome="pending",
                        velocity_percentile=float(score.velocity_percentile),
                        peer_count=int(score.peer_count),
                        peer_vph_median=float(score.peer_vph_median),
                        peer_vph_p85=float(score.peer_vph_p85),
                        peer_vph_p90=float(score.peer_vph_p90),
                        market_median_eur=score.market_median_eur,
                        market_cohort_size=int(score.market_cohort_size),
                        price_delta_pct=score.price_delta_pct,
                        predicted_3h_low=int(score.predicted_3h_low),
                        predicted_3h_high=int(score.predicted_3h_high),
                        predicted_6h_low=int(score.predicted_6h_low),
                        predicted_6h_high=int(score.predicted_6h_high),
                        reasons_json=json.dumps(list(score.reasons), ensure_ascii=False),
                        latest_reasons_json="[]",
                    )
                    session.add(candidate)
                    await session.flush()
                    if not candidate.is_control:
                        created_candidate_ids.append(int(candidate.id))
                    if not candidate.is_control and candidate.stage == "early_winner":
                        session.add(AIEarlyWinnerEvent(
                            candidate_id=int(candidate.id),
                            event_type="winner",
                            payload_json=json.dumps({"from": "initial", "score": candidate.current_score, "type": candidate.opportunity_type}, ensure_ascii=False),
                        ))
                    for hours in AI_CHECKPOINT_HOURS:
                        session.add(AIEarlyWinnerObservation(
                            candidate_id=int(candidate.id),
                            target_hours=int(hours),
                            due_at=snap.captured_at + timedelta(hours=int(hours)),
                            status="pending",
                        ))
                db_run.status = "done"
                db_run.finished_at = datetime.utcnow()
                await session.commit()

            # v4.10.0 DT Radar: every real AI candidate becomes a persistent
            # product signal. Radar has its own append-only history and never
            # deletes products when an AI candidate later cools or resolves.
            for candidate_id in created_candidate_ids:
                try:
                    await record_ai_candidate(
                        candidate_id, source_key=f"ai-initial:{candidate_id}", source="ai_initial"
                    )
                except Exception:
                    log.exception("DT Radar initial AI merge failed candidate=%s", candidate_id)

            self.analyzed_runs += 1
            self.created_candidates += len(selected_ids)
            log.info(
                "AI analyzed scan=%s listings=%s eligible=%s candidates=%s controls=%s",
                scan.id, len(pairs), len(features), len(selected_ids) - len(controls), len(controls),
            )
        except Exception as exc:
            self.last_error = f"analyze scan {scan.id}: {type(exc).__name__}: {exc}"
            log.exception("AI scan analysis failed scan=%s", scan.id)
            if run is not None:
                try:
                    async with SessionLocal() as session:
                        db_run = await session.get(AIEarlyWinnerRun, int(run.id))
                        if db_run is not None:
                            db_run.status = "error"
                            db_run.finished_at = datetime.utcnow()
                            db_run.error_text = self.last_error[:1000]
                            await session.commit()
                except Exception:
                    log.exception("Could not persist AI run error scan=%s", scan.id)

    async def _persist_observation(
        self,
        observation_id: int,
        *,
        views: int,
        measured_at: datetime,
        source: str,
    ) -> None:
        async with SessionLocal() as session:
            obs = await session.get(AIEarlyWinnerObservation, int(observation_id))
            if obs is None:
                return
            candidate = await session.get(AIEarlyWinnerCandidate, int(obs.candidate_id))
            if candidate is None:
                obs.status = "error"
                obs.error_text = "candidate missing"
                obs.completed_at = datetime.utcnow()
                await session.commit()
                return
            listing = (await session.execute(select(Listing).where(
                Listing.external_id == candidate.external_id,
                Listing.is_promoted.is_(False),
                Listing.is_price_reduced.is_(False),
            ))).scalar_one_or_none()
            if listing is None:
                obs.status = "error"
                obs.error_text = "listing missing"
                obs.completed_at = datetime.utcnow()
                await session.commit()
                return

            elapsed_hours = max(0.25, (measured_at - candidate.baseline_at).total_seconds() / 3600.0)
            previous_elapsed_hours = None
            previous_views = None
            if candidate.latest_at is not None and candidate.latest_at > candidate.baseline_at:
                previous_elapsed_hours = max(0.0, (candidate.latest_at - candidate.baseline_at).total_seconds() / 3600.0)
                previous_views = int(candidate.latest_views or candidate.baseline_views or 0)
            dynamic = update_dynamic_score(
                initial_score=int(candidate.initial_score or 0),
                initial_views_per_hour=float(candidate.initial_views_per_hour or 0.0),
                baseline_views=int(candidate.baseline_views or 0),
                current_views=int(views),
                elapsed_hours=elapsed_hours,
                peer_vph_median=float(candidate.peer_vph_median or 0.0),
                peer_vph_p85=float(candidate.peer_vph_p85 or 0.0),
                target_hours=int(obs.target_hours),
                previous_views=previous_views,
                previous_elapsed_hours=previous_elapsed_hours,
                repeatability=float(0.5 if candidate.repeatability is None else candidate.repeatability),
                repeatability_available=(
                    candidate.repeatability is not None
                    and abs(float(candidate.repeatability) - 0.5) >= 0.025
                ),
                price_eur=listing.price_eur,
                market_median_eur=candidate.market_median_eur,
                market_cohort_size=int(candidate.market_cohort_size or 0),
            )

            old_stage = candidate.stage
            old_outcome = candidate.outcome
            candidate.latest_views = int(views)
            candidate.latest_at = measured_at
            candidate.current_score = int(dynamic.score)
            # Confidence here means amount/quality of evidence, not a calibrated
            # probability. Each real checkpoint makes the shadow verdict stronger.
            evidence_bonus = 5 if int(obs.target_hours) <= 1 else 10 if int(obs.target_hours) <= 3 else 15
            candidate.confidence = min(98, int(candidate.confidence or 0) + evidence_bonus)
            candidate.stage = dynamic.stage
            # Reclassify the *reason* for the opportunity as live evidence arrives.
            # High saturation is allowed to become Hot Product when momentum proves it.
            sat = int(candidate.saturation_score or 0)
            if dynamic.score >= 82 and sat >= 65 and dynamic.momentum_ratio >= 1.15:
                candidate.opportunity_type = "hot_product"
            elif dynamic.score >= 84 and sat <= 45 and float(candidate.repeatability or 0.0) >= 0.42:
                candidate.opportunity_type = "hidden_gem"
            elif dynamic.score >= 82 and float(candidate.demand_supply_ratio or 1.0) >= 1.12 and dynamic.momentum_ratio >= 1.08:
                candidate.opportunity_type = "emerging"
            elif sat >= 70 and dynamic.momentum_ratio < 1.05:
                candidate.opportunity_type = "saturated"
            elif candidate.opportunity_type not in {"hidden_gem", "hot_product", "emerging"}:
                candidate.opportunity_type = "spark"
            live_reasons = list(dynamic.reasons)
            live_reasons.append(f"тип сигнала сейчас: {candidate.opportunity_type}")
            candidate.latest_reasons_json = json.dumps(live_reasons, ensure_ascii=False)
            if old_outcome == "pending" and dynamic.outcome in {"confirmed", "rejected"}:
                candidate.outcome = dynamic.outcome
                candidate.resolved_at = measured_at
                if dynamic.outcome == "confirmed":
                    candidate.confirmed_at = measured_at

            obs.status = "done"
            obs.completed_at = datetime.utcnow()
            obs.measured_at = measured_at
            obs.view_count = int(views)
            obs.delta_views = max(0, int(views) - int(candidate.baseline_views or 0))
            obs.observed_views_per_hour = float(dynamic.observed_views_per_hour)
            obs.score_after = int(dynamic.score)
            obs.source = source[:40]
            obs.error_text = None

            # Fresh AI measurements are shared back into the normal analytics DB,
            # so another scan/checkpoint can reuse them instead of hitting the site.
            # This conditional UPDATE is cross-process safe: an AI transaction can
            # never overwrite a newer measurement committed by the Main Bot.
            write_result = await session.execute(
                update(Listing)
                .where(
                    Listing.external_id == listing.external_id,
                    Listing.is_promoted.is_(False),
                    Listing.is_price_reduced.is_(False),
                    or_(
                        Listing.views_checked_at.is_(None),
                        Listing.views_checked_at <= measured_at,
                    ),
                )
                .values(view_count=int(views), views_checked_at=measured_at)
                .execution_options(synchronize_session=False)
            )
            if int(write_result.rowcount or 0) > 0:
                session.add(ViewHistory(
                    external_id=listing.external_id,
                    view_count=int(views),
                    recorded_at=measured_at,
                ))

            if not candidate.is_control:
                if old_stage != "early_winner" and candidate.stage == "early_winner":
                    session.add(AIEarlyWinnerEvent(
                        candidate_id=int(candidate.id), event_type="winner",
                        payload_json=json.dumps({"from": old_stage, "score": candidate.current_score, "type": candidate.opportunity_type}, ensure_ascii=False),
                    ))
                if old_outcome != candidate.outcome and candidate.outcome == "confirmed":
                    # v4.6.2: only strong/positive events feed the unread AI Lab badge.
                    # Rejected candidates stay visible in the Lab, but never create notification noise.
                    session.add(AIEarlyWinnerEvent(
                        candidate_id=int(candidate.id), event_type="confirmed",
                        payload_json=json.dumps({"score": candidate.current_score, "target_hours": obs.target_hours, "type": candidate.opportunity_type}, ensure_ascii=False),
                    ))
            radar_candidate_id = int(candidate.id)
            radar_observation_id = int(obs.id)
            await session.commit()

        # Persist the changed AI score into DT Radar after the AI transaction is
        # committed, so users can see the rating move without touching AI worker
        # correctness if Radar ever has a transient DB/UI error.
        try:
            await record_ai_candidate(
                radar_candidate_id,
                source_key=f"ai-observation:{radar_observation_id}",
                source="ai_observation",
            )
        except Exception:
            log.exception("DT Radar AI observation merge failed candidate=%s obs=%s", radar_candidate_id, radar_observation_id)

        self.observations_done += 1

    async def _requeue_or_fail(self, observation_id: int, error: str) -> None:
        async with SessionLocal() as session:
            obs = await session.get(AIEarlyWinnerObservation, int(observation_id))
            if obs is None:
                return
            if int(obs.attempts or 0) >= AI_MAX_ATTEMPTS:
                obs.status = "error"
                obs.completed_at = datetime.utcnow()
            else:
                obs.status = "pending"
                obs.started_at = None
                obs.due_at = datetime.utcnow() + timedelta(minutes=AI_RETRY_MINUTES)
            obs.error_text = error[:1000]
            await session.commit()

    async def process_due_observations(self) -> None:
        now = datetime.utcnow()
        async with SessionLocal() as session:
            rows = (await session.execute(
                select(AIEarlyWinnerObservation, AIEarlyWinnerCandidate, Listing)
                .join(AIEarlyWinnerCandidate, AIEarlyWinnerCandidate.id == AIEarlyWinnerObservation.candidate_id)
                .join(Listing, Listing.external_id == AIEarlyWinnerCandidate.external_id)
                .where(
                    AIEarlyWinnerObservation.status == "pending",
                    AIEarlyWinnerObservation.due_at <= now,
                    Listing.is_promoted.is_(False),
                    Listing.is_price_reduced.is_(False),
                )
                .order_by(AIEarlyWinnerObservation.due_at.asc())
                .limit(AI_OBSERVATION_BATCH)
            )).all()

        if not rows:
            self.paused_for_scans = False
            return

        reusable: list[tuple[int, int, datetime]] = []
        remote_rows: list[tuple[AIEarlyWinnerObservation, AIEarlyWinnerCandidate, Listing]] = []
        reuse_cutoff_delta = timedelta(minutes=AI_REUSE_WINDOW_MINUTES)
        late_grace = timedelta(minutes=AI_OBSERVATION_LATE_GRACE_MINUTES)
        for obs, candidate, listing in rows:
            if now - obs.due_at > late_grace and int(obs.attempts or 0) == 0:
                async with SessionLocal() as session:
                    db_obs = await session.get(AIEarlyWinnerObservation, int(obs.id))
                    if db_obs is not None and db_obs.status == "pending":
                        db_obs.status = "missed"
                        db_obs.completed_at = now
                        db_obs.error_text = "checkpoint missed while AI worker was offline/busy"
                        await session.commit()
                continue
            if (
                listing.view_count is not None
                and listing.views_checked_at is not None
                and listing.views_checked_at >= obs.due_at - reuse_cutoff_delta
                and listing.views_checked_at > candidate.baseline_at
            ):
                reusable.append((int(obs.id), int(listing.view_count), listing.views_checked_at))
            else:
                remote_rows.append((obs, candidate, listing))

        for obs_id, views, measured_at in reusable:
            await self._persist_observation(obs_id, views=views, measured_at=measured_at, source="shared-db")
            self.observations_reused += 1

        if not remote_rows:
            self.paused_for_scans = False
            return

        active_scans = await self.active_user_scans()
        if AI_PAUSE_DURING_USER_SCANS and active_scans > 0:
            self.paused_for_scans = True
            return
        self.paused_for_scans = False

        # Claim only the rows that really need a network measurement.
        claimed: list[tuple[int, str]] = []
        async with SessionLocal() as session:
            for obs, _candidate, listing in remote_rows:
                db_obs = await session.get(AIEarlyWinnerObservation, int(obs.id))
                if db_obs is None or db_obs.status != "pending":
                    continue
                db_obs.status = "running"
                db_obs.started_at = now
                db_obs.attempts = int(db_obs.attempts or 0) + 1
                claimed.append((int(db_obs.id), listing.url))
            await session.commit()

        if not claimed:
            return
        urls = [url for _, url in claimed if url]
        if not urls or not await REMOTE_VIEW_MANAGER.worker_alive():
            for obs_id, _ in claimed:
                await self._requeue_or_fail(obs_id, "View Worker unavailable")
            return

        results = await REMOTE_VIEW_MANAGER.fetch(urls, traffic_priority="background")
        if results is None:
            for obs_id, _ in claimed:
                await self._requeue_or_fail(obs_id, "remote view batch unavailable")
            return

        measured_at = datetime.utcnow()
        for obs_id, url in claimed:
            vr = results.get(url)
            if vr is None or vr.views is None:
                await self._requeue_or_fail(obs_id, (vr.error if vr else "no exact view result") or "no exact view result")
                continue
            await self._persist_observation(
                obs_id,
                views=int(vr.views),
                measured_at=measured_at,
                source=str(vr.source or "remote")[:40],
            )
            self.observations_remote += 1

    async def loop(self) -> None:
        if not AI_ENABLED:
            log.warning("AI_EARLY_WINNER_ENABLED=0; worker idle")
        while True:
            try:
                await self.heartbeat()
                if AI_ENABLED:
                    # Initial scoring is DB/CPU-only and safe even while scans run.
                    for _ in range(2):
                        scan = await self.find_scan_to_analyze()
                        if scan is None:
                            break
                        await self.analyze_scan(scan)
                    # Network checkpoints pause completely during user scans unless a
                    # fresh exact value can be reused from PostgreSQL.
                    await self.process_due_observations()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("AI worker loop error")
            await asyncio.sleep(AI_POLL_SECONDS)

    async def close(self) -> None:
        try:
            await REMOTE_VIEW_MANAGER.close()
        except Exception:
            pass
        try:
            await self.redis.aclose()
        except Exception:
            pass


async def main() -> None:
    worker = AIWorker()
    await worker.setup()
    try:
        await worker.loop()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
