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
    url: Mapped[str] = mapped_column(String(1200), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    price_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    scan_date: Mapped[str] = mapped_column(String(10), index=True)  # Europe/Berlin YYYY-MM-DD
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
