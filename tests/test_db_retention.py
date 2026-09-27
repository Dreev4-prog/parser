import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import db_retention
from models import (
    Base,
    Listing,
    PriceHistory,
    RadarCheckpointEvent,
    RadarProductListing,
    ScanListing,
    StableCategoryJob,
    StableDateIndex,
    StablePageCheckpoint,
    ViewHistory,
)


class AsyncSyncSession:
    def __init__(self, engine):
        self.session = Session(engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, _tb):
        if exc_type:
            self.session.rollback()
        self.session.close()

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_retention, "SessionLocal", lambda: AsyncSyncSession(engine))
    yield engine
    engine.dispose()


def run(coro):
    return asyncio.run(coro)


def listing(external_id: str, seen_at: datetime) -> Listing:
    return Listing(
        external_id=external_id,
        category="Test",
        title=external_id,
        url=f"https://example.test/{external_id}",
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        is_active=True,
    )


def test_bounded_cache_and_history_retention(database):
    now = datetime.utcnow()
    with Session(database) as session:
        session.add_all([
            RadarCheckpointEvent(
                external_id="old-event", category_key="test", baseline_at=now - timedelta(days=10),
                checkpoint_no=0, event_type="baseline", created_at=now - timedelta(days=10),
            ),
            RadarCheckpointEvent(
                external_id="new-event", category_key="test", baseline_at=now,
                checkpoint_no=0, event_type="baseline", created_at=now,
            ),
            StablePageCheckpoint(
                category_key="old", target_date="2026-01-01", feed_key="feed", page_no=1,
                checked_at=now - timedelta(days=2),
            ),
            StablePageCheckpoint(
                category_key="new", target_date="2026-01-02", feed_key="feed", page_no=1,
                checked_at=now,
            ),
            StableDateIndex(
                category_key="old", target_date="2026-01-01", feed_key="feed",
                updated_at=now - timedelta(days=2),
            ),
            StableCategoryJob(
                job_key="old", category_key="old", target_date="2026-01-01",
                page_limit=1, status="done", created_at=now - timedelta(days=9),
                updated_at=now - timedelta(days=9),
            ),
            ViewHistory(external_id="old-view", view_count=1, recorded_at=now - timedelta(days=40)),
            ViewHistory(external_id="new-view", view_count=2, recorded_at=now),
        ])
        session.commit()

    assert run(db_retention.prune_expired_radar_events(retention_days=7, limit=10)) == 1
    stable = run(db_retention.prune_expired_stable_cache(retention_hours=24, limit=10))
    assert stable == {"stable_pages": 1, "stable_dates": 1, "stable_jobs": 1}
    assert run(db_retention.prune_old_view_history(retention_days=30, limit=10)) == 1

    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(RadarCheckpointEvent)) == 1
        assert session.scalar(select(func.count()).select_from(StablePageCheckpoint)) == 1
        assert session.scalar(select(func.count()).select_from(ViewHistory)) == 1


def test_cold_listing_cleanup_preserves_saved_and_radar_references(database):
    now = datetime.utcnow()
    old = now - timedelta(days=90)
    with Session(database) as session:
        session.add_all([
            listing("cold", old),
            listing("saved", old),
            listing("radar", old),
            listing("recent", now),
            ScanListing(scan_id=1, external_id="saved", captured_at=old),
            RadarProductListing(product_id=1, external_id="radar", first_seen_at=old, last_seen_at=old),
            PriceHistory(external_id="cold", price_text="10", price_eur=10, recorded_at=old),
            ViewHistory(external_id="cold", view_count=1, recorded_at=old),
        ])
        session.commit()

    stats = run(db_retention.prune_unreferenced_cold_listings(retention_days=60, limit=10))
    assert stats == {"cold_listings": 1, "cold_prices": 1, "cold_views": 1}

    with Session(database) as session:
        remaining = set(session.scalars(select(Listing.external_id)))
        assert remaining == {"saved", "radar", "recent"}
        assert session.scalar(select(func.count()).select_from(PriceHistory)) == 0
        assert session.scalar(select(func.count()).select_from(ViewHistory)) == 0
