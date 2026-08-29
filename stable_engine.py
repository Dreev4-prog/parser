from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from models import StableCategoryJob, StableDateIndex, StablePageCheckpoint
from parser import CategoryPageInfo
from page_manager import deserialize_page_info


STABLE_PAGE_CHECKPOINT_TTL_SECONDS = max(
    60, int(os.getenv("STABLE_PAGE_CHECKPOINT_TTL_SECONDS", "300"))
)
STABLE_DATE_INDEX_TTL_SECONDS = max(
    60, int(os.getenv("STABLE_DATE_INDEX_TTL_SECONDS", "900"))
)
# v4.20.0 audited promotion semantics: reject every older parsed card payload,
# including cards that could have been falsely classified from product URL words.
STABLE_PAGE_PAYLOAD_SCHEMA = "v4200-core2-audit3"


def feed_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest()[:24]


def category_job_key(category_key: str, target_date: str, page_limit: int) -> str:
    raw = f"{category_key}|{target_date}|{int(page_limit)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _serialize_page_info(info: CategoryPageInfo) -> str:
    payload = {
        "schema": STABLE_PAGE_PAYLOAD_SCHEMA,
        "requested_page": info.requested_page,
        "final_url": info.final_url,
        "items": [
            {
                "external_id": item.external_id,
                "title": item.title,
                "price_text": item.price_text,
                "price_eur": item.price_eur,
                "url": item.url,
                "posted_text": item.posted_text,
                "is_price_reduced": bool(getattr(item, "is_price_reduced", False)),
            }
            for item in info.items
        ],
        "result_start": info.result_start,
        "result_end": info.result_end,
        "total_results": info.total_results,
        "actual_page": info.actual_page,
        "max_page": info.max_page,
        "request_matches_page": info.request_matches_page,
        "page_verified": info.page_verified,
        "fingerprint": info.fingerprint,
        "raw_candidates": info.raw_candidates,
        "promoted_filtered": info.promoted_filtered,
        "promoted_ids": info.promoted_ids or [],
        "price_reduced_filtered": info.price_reduced_filtered,
        "price_reduced_ids": info.price_reduced_ids or [],
        "duplicate_cards": info.duplicate_cards,
        "missing_date_count": info.missing_date_count,
        "missing_price_count": info.missing_price_count,
        "date_coverage": info.date_coverage,
        "suspicious": info.suspicious,
        "warnings": info.warnings or [],
        "location_shards": info.location_shards or [],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _deserialize_page_info(raw: str) -> CategoryPageInfo:
    payload = json.loads(raw)
    if str(payload.pop("schema", "")) != STABLE_PAGE_PAYLOAD_SCHEMA:
        raise ValueError("stale stable-page payload schema")
    # Reuse the same fail-closed contract as Redis Page Worker payloads.  Durable
    # checkpoints are a cache, not authority: identity/host mismatch triggers a
    # fresh page fetch instead of replaying poisoned parsed data.
    info = deserialize_page_info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if info is None:
        raise ValueError("invalid stable-page payload identity")
    return info


async def load_page_checkpoint(
    category_key: str,
    target_date: str,
    feed_url: str,
    page_no: int,
) -> CategoryPageInfo | None:
    cutoff = datetime.utcnow() - timedelta(seconds=STABLE_PAGE_CHECKPOINT_TTL_SECONDS)
    key = feed_key(feed_url)
    async with SessionLocal() as session:
        result = await session.execute(
            select(StablePageCheckpoint).where(
                StablePageCheckpoint.category_key == category_key,
                StablePageCheckpoint.target_date == target_date,
                StablePageCheckpoint.feed_key == key,
                StablePageCheckpoint.page_no == int(page_no),
                StablePageCheckpoint.status == "verified",
                StablePageCheckpoint.checked_at >= cutoff,
            )
        )
        row = result.scalar_one_or_none()
        if row is None or not row.payload_json:
            return None
        try:
            return _deserialize_page_info(row.payload_json)
        except Exception:
            return None


async def save_page_checkpoint(
    category_key: str,
    target_date: str,
    feed_url: str,
    page_no: int,
    info: CategoryPageInfo,
    *,
    relation: str,
) -> None:
    key = feed_key(feed_url)
    now = datetime.utcnow()
    payload = _serialize_page_info(info)

    async def apply(session, row: StablePageCheckpoint) -> None:
        row.status = "verified"
        row.relation = relation[:24]
        row.payload_json = payload
        row.checked_at = now
        row.date_coverage = int(round(float(info.date_coverage or 0.0) * 100))
        row.fingerprint = (info.fingerprint or "")[:80]
        row.attempts = max(1, int(row.attempts or 0))
        row.error_text = ""

    async with SessionLocal() as session:
        result = await session.execute(
            select(StablePageCheckpoint).where(
                StablePageCheckpoint.category_key == category_key,
                StablePageCheckpoint.target_date == target_date,
                StablePageCheckpoint.feed_key == key,
                StablePageCheckpoint.page_no == int(page_no),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = StablePageCheckpoint(
                category_key=category_key, target_date=target_date,
                feed_key=key, page_no=int(page_no),
            )
            session.add(row)
        await apply(session, row)
        try:
            await session.commit()
        except IntegrityError:
            # Different depth jobs may discover the same page concurrently. The
            # unique key makes that safe; the loser simply updates the winner.
            await session.rollback()
            result = await session.execute(
                select(StablePageCheckpoint).where(
                    StablePageCheckpoint.category_key == category_key,
                    StablePageCheckpoint.target_date == target_date,
                    StablePageCheckpoint.feed_key == key,
                    StablePageCheckpoint.page_no == int(page_no),
                )
            )
            row = result.scalar_one()
            await apply(session, row)
            await session.commit()


async def record_page_failure(
    category_key: str,
    target_date: str,
    feed_url: str,
    page_no: int,
    error_text: str,
) -> None:
    key = feed_key(feed_url)
    now = datetime.utcnow()
    async with SessionLocal() as session:
        result = await session.execute(
            select(StablePageCheckpoint).where(
                StablePageCheckpoint.category_key == category_key,
                StablePageCheckpoint.target_date == target_date,
                StablePageCheckpoint.feed_key == key,
                StablePageCheckpoint.page_no == int(page_no),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = StablePageCheckpoint(
                category_key=category_key, target_date=target_date,
                feed_key=key, page_no=int(page_no),
            )
            session.add(row)
        row.status = "failed"
        row.attempts = int(row.attempts or 0) + 1
        row.error_text = error_text[:500]
        row.checked_at = now
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(
                select(StablePageCheckpoint).where(
                    StablePageCheckpoint.category_key == category_key,
                    StablePageCheckpoint.target_date == target_date,
                    StablePageCheckpoint.feed_key == key,
                    StablePageCheckpoint.page_no == int(page_no),
                )
            )
            row = result.scalar_one()
            row.status = "failed"
            row.attempts = int(row.attempts or 0) + 1
            row.error_text = error_text[:500]
            row.checked_at = now
            await session.commit()


async def load_date_index(
    category_key: str,
    target_date: str,
    feed_url: str,
) -> dict[str, Any] | None:
    cutoff = datetime.utcnow() - timedelta(seconds=STABLE_DATE_INDEX_TTL_SECONDS)
    key = feed_key(feed_url)
    async with SessionLocal() as session:
        result = await session.execute(
            select(StableDateIndex).where(
                StableDateIndex.category_key == category_key,
                StableDateIndex.target_date == target_date,
                StableDateIndex.feed_key == key,
                StableDateIndex.updated_at >= cutoff,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "status": row.status,
            "candidate_page": row.candidate_page,
            "max_page": row.max_page,
            "updated_at": row.updated_at,
        }


async def save_date_index(
    category_key: str,
    target_date: str,
    feed_url: str,
    *,
    status: str,
    candidate_page: int | None = None,
    max_page: int | None = None,
) -> None:
    key = feed_key(feed_url)
    now = datetime.utcnow()

    async def apply(row: StableDateIndex) -> None:
        row.status = status[:24]
        row.candidate_page = candidate_page
        row.max_page = max_page
        row.updated_at = now

    async with SessionLocal() as session:
        result = await session.execute(
            select(StableDateIndex).where(
                StableDateIndex.category_key == category_key,
                StableDateIndex.target_date == target_date,
                StableDateIndex.feed_key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = StableDateIndex(category_key=category_key, target_date=target_date, feed_key=key)
            session.add(row)
        await apply(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(
                select(StableDateIndex).where(
                    StableDateIndex.category_key == category_key,
                    StableDateIndex.target_date == target_date,
                    StableDateIndex.feed_key == key,
                )
            )
            row = result.scalar_one()
            await apply(row)
            await session.commit()


async def mark_category_job(
    category_key: str,
    target_date: str,
    page_limit: int,
    *,
    status: str,
    verified_pages: int = 0,
    network_requests: int = 0,
    matched_count: int = 0,
    error_text: str = "",
) -> None:
    key = category_job_key(category_key, target_date, page_limit)
    now = datetime.utcnow()

    async def apply(row: StableCategoryJob) -> None:
        row.status = status[:24]
        row.updated_at = now
        if status == "running" and row.started_at is None:
            row.started_at = now
        if status in {"done", "partial", "failed"}:
            row.finished_at = now
        row.verified_pages = max(int(row.verified_pages or 0), int(verified_pages or 0))
        row.network_requests = max(int(row.network_requests or 0), int(network_requests or 0))
        row.matched_count = max(int(row.matched_count or 0), int(matched_count or 0))
        row.error_text = (error_text or "")[:1000]

    async with SessionLocal() as session:
        result = await session.execute(select(StableCategoryJob).where(StableCategoryJob.job_key == key))
        row = result.scalar_one_or_none()
        if row is None:
            row = StableCategoryJob(
                job_key=key, category_key=category_key, target_date=target_date,
                page_limit=int(page_limit), created_at=now,
            )
            session.add(row)
        await apply(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(select(StableCategoryJob).where(StableCategoryJob.job_key == key))
            row = result.scalar_one()
            await apply(row)
            await session.commit()


async def cleanup_stable_state(retention_hours: int = 24) -> dict[str, int]:
    """Keep high-frequency page checkpoints bounded; listings remain in PostgreSQL."""
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(retention_hours)))
    job_cutoff = datetime.utcnow() - timedelta(days=7)
    async with SessionLocal() as session:
        pages = await session.execute(
            delete(StablePageCheckpoint).where(StablePageCheckpoint.checked_at < cutoff)
        )
        indexes = await session.execute(
            delete(StableDateIndex).where(StableDateIndex.updated_at < cutoff)
        )
        jobs = await session.execute(
            delete(StableCategoryJob).where(
                StableCategoryJob.updated_at < job_cutoff,
                StableCategoryJob.status.in_(["done", "partial", "failed"]),
            )
        )
        await session.commit()
        return {
            "pages": int(pages.rowcount or 0),
            "indexes": int(indexes.rowcount or 0),
            "jobs": int(jobs.rowcount or 0),
        }
