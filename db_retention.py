from __future__ import annotations

import os
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from db import SessionLocal
from models import (
    AIEarlyWinnerCandidate,
    Listing,
    PriceHistory,
    RadarCheckpointEvent,
    RadarLifecycleEvent,
    RadarLifecycleWatch,
    RadarObservation,
    RadarProductListing,
    RadarSnapshot,
    ScanListing,
    ScanViewHistory,
    StableCategoryJob,
    StableDateIndex,
    StablePageCheckpoint,
    ViewHistory,
)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


DB_RETENTION_ENABLED = os.getenv("DB_RETENTION_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
DB_RETENTION_INTERVAL_SECONDS = _env_int(
    "DB_RETENTION_INTERVAL_SECONDS", 900, 60, 3600
)
DB_RETENTION_STARTUP_DELAY_SECONDS = _env_int(
    "DB_RETENTION_STARTUP_DELAY_SECONDS", 45, 5, 600
)

# These defaults intentionally match the product's existing retention promises.
RADAR_EVENT_RETENTION_DAYS = _env_int("RADAR_EVENT_RETENTION_DAYS", 7, 2, 30)
STABLE_CACHE_RETENTION_HOURS = _env_int("STABLE_CACHE_RETENTION_HOURS", 24, 1, 168)
VIEW_HISTORY_RETENTION_DAYS = _env_int("VIEW_HISTORY_RETENTION_DAYS", 30, 7, 180)
COLD_LISTING_RETENTION_DAYS = _env_int("COLD_LISTING_RETENTION_DAYS", 60, 30, 365)

# Small transactions let PostgreSQL autovacuum reuse pages without long locks.
RADAR_EVENT_RETENTION_BATCH = _env_int("RADAR_EVENT_RETENTION_BATCH", 10_000, 100, 25_000)
STABLE_CACHE_RETENTION_BATCH = _env_int("STABLE_CACHE_RETENTION_BATCH", 2_000, 100, 10_000)
VIEW_HISTORY_RETENTION_BATCH = _env_int("VIEW_HISTORY_RETENTION_BATCH", 10_000, 100, 25_000)
COLD_LISTING_RETENTION_BATCH = _env_int("COLD_LISTING_RETENTION_BATCH", 500, 10, 2_000)


async def _delete_old_rows(model, timestamp_column, cutoff: datetime, limit: int) -> int:
    ids = (
        select(model.id)
        .where(timestamp_column < cutoff)
        .order_by(model.id.asc())
        .limit(max(1, int(limit)))
    )
    async with SessionLocal() as session:
        result = await session.execute(delete(model).where(model.id.in_(ids)))
        await session.commit()
        return int(result.rowcount or 0)


async def prune_expired_radar_events(
    *, retention_days: int = RADAR_EVENT_RETENTION_DAYS,
    limit: int = RADAR_EVENT_RETENTION_BATCH,
) -> int:
    """Delete only the bounded Radar audit journal, never observations or products."""
    cutoff = datetime.utcnow() - timedelta(days=max(2, int(retention_days)))
    return await _delete_old_rows(RadarCheckpointEvent, RadarCheckpointEvent.created_at, cutoff, limit)


async def prune_expired_stable_cache(
    *, retention_hours: int = STABLE_CACHE_RETENTION_HOURS,
    limit: int = STABLE_CACHE_RETENTION_BATCH,
) -> dict[str, int]:
    """Prune disposable page/date cache state in bounded transactions."""
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(retention_hours)))
    job_cutoff = datetime.utcnow() - timedelta(days=7)
    batch = max(1, int(limit))

    page_ids = (
        select(StablePageCheckpoint.id)
        .where(StablePageCheckpoint.checked_at < cutoff)
        .order_by(StablePageCheckpoint.id.asc())
        .limit(batch)
    )
    date_ids = (
        select(StableDateIndex.id)
        .where(StableDateIndex.updated_at < cutoff)
        .order_by(StableDateIndex.id.asc())
        .limit(batch)
    )
    job_ids = (
        select(StableCategoryJob.id)
        .where(
            StableCategoryJob.updated_at < job_cutoff,
            StableCategoryJob.status.in_(["done", "partial", "failed"]),
        )
        .order_by(StableCategoryJob.id.asc())
        .limit(batch)
    )

    async with SessionLocal() as session:
        pages = await session.execute(
            delete(StablePageCheckpoint).where(StablePageCheckpoint.id.in_(page_ids))
        )
        dates = await session.execute(
            delete(StableDateIndex).where(StableDateIndex.id.in_(date_ids))
        )
        jobs = await session.execute(
            delete(StableCategoryJob).where(StableCategoryJob.id.in_(job_ids))
        )
        await session.commit()
        return {
            "stable_pages": int(pages.rowcount or 0),
            "stable_dates": int(dates.rowcount or 0),
            "stable_jobs": int(jobs.rowcount or 0),
        }


async def prune_old_view_history(
    *, retention_days: int = VIEW_HISTORY_RETENTION_DAYS,
    limit: int = VIEW_HISTORY_RETENTION_BATCH,
) -> int:
    """Bound global raw counters; per-user ScanViewHistory is preserved."""
    cutoff = datetime.utcnow() - timedelta(days=max(7, int(retention_days)))
    return await _delete_old_rows(ViewHistory, ViewHistory.recorded_at, cutoff, limit)


def _cold_listing_unreferenced_conditions() -> tuple:
    """Protect every user-visible, Radar and legacy analytics reference."""
    reference_models = (
        ScanListing,
        ScanViewHistory,
        AIEarlyWinnerCandidate,
        RadarProductListing,
        RadarObservation,
        RadarCheckpointEvent,
        RadarLifecycleWatch,
        RadarLifecycleEvent,
        RadarSnapshot,
    )
    return tuple(
        ~select(model.id).where(model.external_id == Listing.external_id).exists()
        for model in reference_models
    )


async def prune_unreferenced_cold_listings(
    *, retention_days: int = COLD_LISTING_RETENTION_DAYS,
    limit: int = COLD_LISTING_RETENTION_BATCH,
) -> dict[str, int]:
    """Remove only cold listings that no saved scan or Radar record can display.

    Price/View history for the selected IDs is removed in the same transaction.
    Sticky ListingIntegrity rows are deliberately retained so a promoted listing
    cannot re-enter organic analytics after it is rediscovered.
    """
    cutoff = datetime.utcnow() - timedelta(days=max(30, int(retention_days)))
    conditions = _cold_listing_unreferenced_conditions()
    candidate_ids = (
        select(Listing.external_id)
        .where(Listing.last_seen_at < cutoff, *conditions)
        .order_by(Listing.id.asc())
        .limit(max(1, int(limit)))
    )

    async with SessionLocal() as session:
        external_ids = [
            str(value)
            for value in (await session.execute(candidate_ids)).scalars().all()
        ]
        if not external_ids:
            return {"cold_listings": 0, "cold_prices": 0, "cold_views": 0}

        # Re-check all references in the DELETE itself. This keeps a listing that
        # became user/Radar-visible after candidate selection.
        deleted_listings = await session.execute(
            delete(Listing).where(
                Listing.external_id.in_(external_ids),
                Listing.last_seen_at < cutoff,
                *_cold_listing_unreferenced_conditions(),
            ).returning(Listing.external_id)
        )
        deleted_external_ids = [str(value) for value in deleted_listings.scalars().all()]
        if not deleted_external_ids:
            await session.rollback()
            return {"cold_listings": 0, "cold_prices": 0, "cold_views": 0}

        prices = await session.execute(
            delete(PriceHistory).where(PriceHistory.external_id.in_(deleted_external_ids))
        )
        views = await session.execute(
            delete(ViewHistory).where(ViewHistory.external_id.in_(deleted_external_ids))
        )
        await session.commit()
        return {
            "cold_listings": len(deleted_external_ids),
            "cold_prices": int(prices.rowcount or 0),
            "cold_views": int(views.rowcount or 0),
        }


async def run_database_retention_once() -> dict[str, int]:
    """Run one bounded maintenance pass; safe to call repeatedly."""
    stats: dict[str, int] = {
        "radar_events": await prune_expired_radar_events(),
        "view_history": await prune_old_view_history(),
    }
    stats.update(await prune_expired_stable_cache())
    stats.update(await prune_unreferenced_cold_listings())
    return stats
