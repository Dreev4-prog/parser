from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500))
    price_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    posted_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    posted_date_msk: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(1200), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    views_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # v3.0 deterministic product identity. These columns are intentionally
    # denormalized on the listing so analytics do not have to re-parse titles.
    identity_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    identity_label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    identity_brand: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    identity_model: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    identity_variant: Mapped[str | None] = mapped_column(String(180), nullable=True)
    identity_product_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    identity_storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    identity_ram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_specs: Mapped[str | None] = mapped_column(String(300), nullable=True)
    identity_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    price_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ViewHistory(Base):
    __tablename__ = "view_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    view_count: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SelectedCategory(Base):
    __tablename__ = "selected_categories"
    __table_args__ = (UniqueConstraint("user_id", "category_key", name="uq_user_category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    category_key: Mapped[str] = mapped_column(String(80), index=True)


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    output_mode: Mapped[str] = mapped_column(String(32), default="newest")
    smart_dedupe: Mapped[bool] = mapped_column(Boolean, default=True)
    clean_noise: Mapped[bool] = mapped_column(Boolean, default=True)
    period: Mapped[str] = mapped_column(String(16), default="today")
    price_filter: Mapped[str] = mapped_column(String(32), default="any")
    sort_mode: Mapped[str] = mapped_column(String(32), default="newest")
    include_words: Mapped[str] = mapped_column(String(1000), default="")
    exclude_words: Mapped[str] = mapped_column(String(1000), default="")
    # Maximum number of result pages the user wants refreshed per category.
    page_limit: Mapped[int] = mapped_column(Integer, default=100)


class CategoryScanState(Base):
    """Small per-category checkpoint used by v2.5+/v2.6 incremental scans."""

    __tablename__ = "category_scan_state"

    category_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    scan_date: Mapped[str] = mapped_column(String(10), index=True)  # worker-day key
    target_date: Mapped[str] = mapped_column(String(10), default="", index=True)  # Europe/Moscow YYYY-MM-DD
    head_ids: Mapped[str] = mapped_column(Text, default="")
    last_scan_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_mode: Mapped[str] = mapped_column(String(16), default="full")
    day_seed_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when the daily seed stopped only because the user/page cap was reached.
    # This lets a later user request a deeper scan without treating a shallow seed as complete.
    day_seed_capped: Mapped[bool] = mapped_column(Boolean, default=False)
    day_full_pages: Mapped[int] = mapped_column(Integer, default=0)
    last_pages: Mapped[int] = mapped_column(Integer, default=0)
    last_new: Mapped[int] = mapped_column(Integer, default=0)
    last_today_seen: Mapped[int] = mapped_column(Integer, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    last_stop_reason: Mapped[str] = mapped_column(String(255), default="")


class ParserRun(Base):
    """History of category scans; useful for Telegram stats and future scheduler work."""

    __tablename__ = "parser_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    category_key: Mapped[str] = mapped_column(String(80), index=True)
    category_name: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    today_seen: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    known_count: Mapped[int] = mapped_column(Integer, default=0)
    enriched_count: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str] = mapped_column(String(255), default="")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class UserScan(Base):
    """Persistent user-facing scan card shown in "Мои сканы"."""

    __tablename__ = "user_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(255))
    category_keys: Mapped[str] = mapped_column(Text, default="")
    page_limit: Mapped[int] = mapped_column(Integer, default=25)
    target_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_view_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    completed_categories: Mapped[int] = mapped_column(Integer, default=0)
    total_categories: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    viewed_count: Mapped[int] = mapped_column(Integer, default=0)
    # v3.0.1 exact-date scanner metadata. A scan can finish partially when
    # Kleinanzeigen temporarily refuses requests or the safety cap is reached
    # before the requested calendar day is fully crossed.
    target_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    scan_note: Mapped[str] = mapped_column(String(500), default="")


class ScanListing(Base):
    """Snapshot membership + view count at the moment a user scan completed."""

    __tablename__ = "scan_listings"
    __table_args__ = (UniqueConstraint("scan_id", "external_id", name="uq_scan_listing"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    initial_view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ScanViewHistory(Base):
    """Per-user-scan view snapshots used for 1/3/6/24h velocity analytics."""

    __tablename__ = "scan_view_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    view_count: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
