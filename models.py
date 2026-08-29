from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
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
    # v4.15.6: earliest publication-day value DT ever observed for this external_id.
    # Unlike posted_date_msk this is never moved forward, so a later date jump on the
    # same id is strong evidence that an old ad resurfaced via paid visibility.
    first_posted_date_msk: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(1200), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Paid Kleinanzeigen visibility feature (Hochschieben/Top/Highlight/Galerie).
    # v4.15.2 treats this as sticky demand contamination: accumulated views from
    # paid visibility cannot become organic again merely because the badge expires.
    is_promoted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # v4.15.2 Organic Demand Integrity. Sticky once detected: a listing that has
    # ever shown an explicit crossed/previous price must never train demand stats.
    is_price_reduced: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    views_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # v4.15.6 Organic Baseline. Raw total views stay auditable in view_count /
    # ViewHistory; this baseline marks the point after which DT directly observed
    # organic growth for listings whose pre-DT history cannot be reconstructed.
    organic_baseline_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    organic_baseline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    organic_history_status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    # v4.15.7 Verified Organic Velocity.  Initial counters >=400 never vote in
    # demand scoring until DT has two later clean exact measurements.
    organic_verified_checkpoints: Mapped[int] = mapped_column(Integer, default=0, index=True)
    organic_last_checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    organic_last_checkpoint_views: Mapped[int | None] = mapped_column(Integer, nullable=True)

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


class ListingIntegrity(Base):
    """Sticky exclusion registry for non-organic demand samples.

    A paid/reduced card may be filtered before a normal Listing row exists. Keeping
    the external id here prevents the same ad from entering analytics later after
    the paid badge/crossed price disappears while its accumulated views remain.
    """

    __tablename__ = "listing_integrity"

    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_promoted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_price_reduced: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # Last sticky promotion reason (search marker, bump icon, resurfaced date, ...).
    promotion_reason: Mapped[str] = mapped_column(String(80), default="")


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
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    category_key: Mapped[str] = mapped_column(String(80), index=True)


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    output_mode: Mapped[str] = mapped_column(String(32), default="newest")
    smart_dedupe: Mapped[bool] = mapped_column(Boolean, default=True)
    clean_noise: Mapped[bool] = mapped_column(Boolean, default=True)
    # Legacy v3.2.6 field kept for schema compatibility; ignored since v3.2.7.
    period: Mapped[str] = mapped_column(String(16), default="today")
    price_filter: Mapped[str] = mapped_column(String(32), default="any")
    min_views: Mapped[int] = mapped_column(Integer, default=0)
    sort_mode: Mapped[str] = mapped_column(String(32), default="newest")
    include_words: Mapped[str] = mapped_column(String(1000), default="")
    exclude_words: Mapped[str] = mapped_column(String(1000), default="")
    # Maximum number of result pages the user wants refreshed per category.
    page_limit: Mapped[int] = mapped_column(Integer, default=50)
    # v4.3.19: +3/+6/+12h control measurements are opt-in.
    auto_observations: Mapped[bool] = mapped_column(Boolean, default=False)


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
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
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
    # v3.1 parser-quality diagnostics. These counters describe the actual HTML
    # responses used by the scan and make silent parser degradation visible.
    cards_seen: Mapped[int] = mapped_column(Integer, default=0)
    listings_parsed: Mapped[int] = mapped_column(Integer, default=0)
    missing_date_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_price_count: Mapped[int] = mapped_column(Integer, default=0)
    promoted_filtered: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_pages: Mapped[int] = mapped_column(Integer, default=0)
    repeated_pages: Mapped[int] = mapped_column(Integer, default=0)
    low_quality_pages: Mapped[int] = mapped_column(Integer, default=0)
    view_failures: Mapped[int] = mapped_column(Integer, default=0)
    quality_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    stop_reason: Mapped[str] = mapped_column(String(255), default="")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class UserScan(Base):
    """Persistent user-facing scan card shown in "Мои сканы"."""

    __tablename__ = "user_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(255))
    category_keys: Mapped[str] = mapped_column(Text, default="")
    page_limit: Mapped[int] = mapped_column(Integer, default=25)
    target_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    # Snapshot price filter selected for this exact scan (v4.3.7+).
    price_filter: Mapped[str] = mapped_column(String(32), default="any")
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
    quality_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    quality_note: Mapped[str] = mapped_column(String(500), default="")
    # v3.2.8: UI archive marker. Scan data/listings/history are preserved;
    # only the My Scans inbox is kept compact.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # v3.3.0 production recovery metadata. Telegram chat/message references let a
    # Railway restart rebuild an unfinished queue entry instead of silently losing it.
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    status_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resumed_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # v3.3.1: exact category keys that need a targeted recheck after a partial run.
    incomplete_category_keys: Mapped[str] = mapped_column(Text, default="")

    # v4.9.0 launch trial. Trial scans are normal full-quality saved scans, but
    # subscription-only follow-up work (repeat/update/auto-observations) stays gated.
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    trial_credit_refunded: Mapped[bool] = mapped_column(Boolean, default=False)


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
    """Per-user-scan view snapshots used for scheduled/manual velocity analytics."""

    __tablename__ = "scan_view_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    view_count: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # None = manual refresh/baseline-compatible point. 3/6/12 = automatic checkpoint.
    target_hours: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class ScanObservation(Base):
    """Persistent automatic observation plan for one saved scan."""

    __tablename__ = "scan_observations"
    __table_args__ = (UniqueConstraint("scan_id", "target_hours", name="uq_scan_observation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(index=True)
    target_hours: Mapped[int] = mapped_column(Integer, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class AIEarlyWinnerRun(Base):
    """One shadow-mode Early Winner analysis for a completed user scan."""

    __tablename__ = "ai_early_winner_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    model_version: Mapped[str] = mapped_column(String(40), default="ew-stat-v1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    control_count: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class AIEarlyWinnerCandidate(Base):
    """Admin-only Early Winner candidate tracked after a normal parser scan."""

    __tablename__ = "ai_early_winner_candidates"
    __table_args__ = (UniqueConstraint("scan_id", "external_id", name="uq_ai_ew_scan_listing"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(index=True)
    scan_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    category_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    identity_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    # v4.6 Product Opportunity Engine: family/type/trend are persisted so the lab can
    # learn from repeat appearances instead of treating every listing in isolation.
    cohort_key: Mapped[str] = mapped_column(String(600), default="", index=True)
    opportunity_type: Mapped[str] = mapped_column(String(32), default="spark", index=True)
    saturation_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    supply_percentile: Mapped[float] = mapped_column(Float, default=0.0)
    supply_growth_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    demand_growth_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    demand_supply_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    repeatability: Mapped[float] = mapped_column(Float, default=0.0)
    is_control: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    baseline_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    baseline_views: Mapped[int] = mapped_column(Integer, default=0)
    age_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    initial_views_per_hour: Mapped[float] = mapped_column(Float, default=0.0)
    latest_views: Mapped[int] = mapped_column(Integer, default=0)
    latest_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    initial_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    current_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    stage: Mapped[str] = mapped_column(String(24), default="watch", index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    velocity_percentile: Mapped[float] = mapped_column(Float, default=0.0)
    peer_count: Mapped[int] = mapped_column(Integer, default=0)
    peer_vph_median: Mapped[float] = mapped_column(Float, default=0.0)
    peer_vph_p85: Mapped[float] = mapped_column(Float, default=0.0)
    peer_vph_p90: Mapped[float] = mapped_column(Float, default=0.0)
    market_median_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cohort_size: Mapped[int] = mapped_column(Integer, default=0)
    price_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_3h_low: Mapped[int] = mapped_column(Integer, default=0)
    predicted_3h_high: Mapped[int] = mapped_column(Integer, default=0)
    predicted_6h_low: Mapped[int] = mapped_column(Integer, default=0)
    predicted_6h_high: Mapped[int] = mapped_column(Integer, default=0)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    latest_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class AIEarlyWinnerObservation(Base):
    """Internal +1/+3/+6h control point for one Early Winner candidate."""

    __tablename__ = "ai_early_winner_observations"
    __table_args__ = (UniqueConstraint("candidate_id", "target_hours", name="uq_ai_ew_candidate_checkpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(index=True)
    target_hours: Mapped[int] = mapped_column(Integer, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delta_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_views_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="")
    error_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class AIEarlyWinnerEvent(Base):
    """Admin notification outbox generated by the AI worker."""

    __tablename__ = "ai_early_winner_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class RadarProduct(Base):
    """Persistent DT Radar product family. Rows are never auto-deleted.

    One Radar product can represent many independent Kleinanzeigen listings of the
    same deterministic identity/family. Scores are allowed to move over time while
    ``peak_score`` preserves the historical maximum.
    """

    __tablename__ = "radar_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_key: Mapped[str] = mapped_column(String(600), unique=True, index=True)
    category_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    representative_external_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    first_radar_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # v4.15.4: user-facing Radar families are visible only after at least one
    # live detail-page organic verification in the strict admission pipeline.
    organic_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # v4.15.6: one-time historical bump sweep verification is separate from
    # Organic Radar certification. Old families stay hidden until a fresh strict
    # v4.15.6 signal rebuilds them, even after their old links were sweep-checked.
    bump_sweep_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_signal_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_signal_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    current_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    peak_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    # v4.20.0 unified 48H Radar: internal ordering is deliberately separate from
    # the public DT Demand Score.  It blends DT Score, evidence confidence and
    # maturity while status is guarded by an absolute demand floor.
    radar_rank: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    demand_views: Mapped[int] = mapped_column(Integer, default=0, index=True)
    demand_age_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    demand_gate: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="stable", index=True)
    opportunity_type: Mapped[str] = mapped_column(String(32), default="spark", index=True)
    signal_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confirmed_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    listing_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    best_views: Mapped[int] = mapped_column(Integer, default=0, index=True)
    best_views_per_hour: Mapped[float] = mapped_column(Float, default=0.0)
    min_price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    max_price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    latest_reason: Mapped[str] = mapped_column(String(800), default="")
    latest_source: Mapped[str] = mapped_column(String(32), default="", index=True)
    last_ai_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RadarProductListing(Base):
    """Distinct live/historical listings that contributed to one Radar product."""

    __tablename__ = "radar_product_listings"
    __table_args__ = (UniqueConstraint("product_id", "external_id", name="uq_radar_product_listing"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    best_views: Mapped[int] = mapped_column(Integer, default=0)
    last_price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RadarObservation(Base):
    """Radar 3.0 listing-level observation anchored only to DT-owned measurements."""

    __tablename__ = "radar_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    product_key: Mapped[str] = mapped_column(String(600), default="", index=True)
    baseline_views: Mapped[int] = mapped_column(Integer, default=0)
    baseline_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_views: Mapped[int] = mapped_column(Integer, default=0)
    last_measured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    checkpoint_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    positive_checkpoints: Mapped[int] = mapped_column(Integer, default=0, index=True)
    consecutive_positive: Mapped[int] = mapped_column(Integer, default=0, index=True)
    total_delta: Mapped[int] = mapped_column(Integer, default=0, index=True)
    current_vph: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    peak_vph: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(String(24), default="baseline", index=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # v4.21.1: cross-replica claim. Only one Parser replica may refresh one
    # Radar observation at a time. The lease is deliberately short and expires
    # automatically after a crashed/deployed worker disappears.
    lease_owner: Mapped[str] = mapped_column(String(120), default="", index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RadarLifecycleWatch(Base):
    """Adaptive availability watch for fresh, strong DT Radar listings.

    The table is intentionally listing-level rather than product-level: one product
    family may have several independent Kleinanzeigen ads with different lifetimes.
    """

    __tablename__ = "radar_lifecycle_watches"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(String(1200), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    radar_started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status: Mapped[str] = mapped_column(String(24), default="watching", index=True)
    tier: Mapped[str] = mapped_column(String(8), default="B", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    peak_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    last_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_step: Mapped[int] = mapped_column(Integer, default=0)
    checks: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_missing: Mapped[int] = mapped_column(Integer, default=0)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    first_missing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    lifetime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    last_result: Mapped[str] = mapped_column(String(32), default="")
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    lease_owner: Mapped[str] = mapped_column(String(120), default="", index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RadarSnapshot(Base):
    """Append-only score/signal history for DT Radar."""

    __tablename__ = "radar_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    scan_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    radar_rank: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    demand_views: Mapped[int] = mapped_column(Integer, default=0)
    demand_age_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    demand_gate: Mapped[int] = mapped_column(Integer, default=0)
    demand_status: Mapped[str] = mapped_column(String(24), default="historical", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="", index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="", index=True)
    opportunity_type: Mapped[str] = mapped_column(String(32), default="", index=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RadarFavorite(Base):
    """Subscriber watch-list entry. Radar data remains global; favorites are per user."""

    __tablename__ = "radar_favorites"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_radar_user_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_id: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class StablePageCheckpoint(Base):
    """Persistent verified category page used by the v3.8 stable scan engine."""

    __tablename__ = "stable_page_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "category_key", "target_date", "feed_key", "page_no",
            name="uq_stable_page_checkpoint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_key: Mapped[str] = mapped_column(String(80), index=True)
    target_date: Mapped[str] = mapped_column(String(10), index=True)
    feed_key: Mapped[str] = mapped_column(String(32), index=True)
    page_no: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="verified", index=True)
    relation: Mapped[str] = mapped_column(String(24), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    date_coverage: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[str] = mapped_column(String(80), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str] = mapped_column(String(500), default="")


class StableDateIndex(Base):
    """Remember the verified page boundary for category/date/feed combinations."""

    __tablename__ = "stable_date_index"
    __table_args__ = (
        UniqueConstraint(
            "category_key", "target_date", "feed_key",
            name="uq_stable_date_index",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_key: Mapped[str] = mapped_column(String(80), index=True)
    target_date: Mapped[str] = mapped_column(String(10), index=True)
    feed_key: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    candidate_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class StableCategoryJob(Base):
    """Shared category/date/depth work record independent from any one Telegram user."""

    __tablename__ = "stable_category_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    category_key: Mapped[str] = mapped_column(String(80), index=True)
    target_date: Mapped[str] = mapped_column(String(10), index=True)
    page_limit: Mapped[int] = mapped_column(Integer, default=25)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    verified_pages: Mapped[int] = mapped_column(Integer, default=0)
    network_requests: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str] = mapped_column(String(1000), default="")


class BotUser(Base):
    """Commercial-service user profile and access state."""

    __tablename__ = "bot_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    access_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payments_count: Mapped[int] = mapped_column(Integer, default=0)
    paid_total_usdt: Mapped[float] = mapped_column(Float, default=0.0)

    # v4.9.0 launch funnel: at most two free scans for never-paid users while
    # the admin-controlled launch promotion is enabled.
    trial_scans_used: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # v3.3.0 product onboarding + subscription lifecycle notifications.
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    expiry_warning_sent_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expiry_expired_sent_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FreeRadarEvent(Base):
    """Privacy-light product analytics for the public DT Radar preview.

    Only Telegram user id and in-product actions are stored. Product clicks keep
    the internal Radar product id so the admin can see whether a visitor actually
    consumed the five-item demo. No message text or external browsing data is
    collected.
    """

    __tablename__ = "free_radar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(24), default="", index=True)
    feature: Mapped[str] = mapped_column(String(40), default="", index=True)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SubscriptionPlan(Base):
    """Admin-editable subscription plans priced in USDT."""

    __tablename__ = "subscription_plans"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    days: Mapped[int] = mapped_column(Integer)
    price_usdt: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SubscriptionPayment(Base):
    """Invoice/payment record for Crypto Pay or xRocket."""

    __tablename__ = "subscription_payments"
    __table_args__ = (
        UniqueConstraint("provider", "external_invoice_id", name="uq_subscription_payment_provider_invoice"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan_key: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    external_invoice_id: Mapped[str] = mapped_column(String(128), index=True)
    amount_usdt: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    pay_url: Mapped[str] = mapped_column(String(1200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    raw_status: Mapped[str] = mapped_column(String(100), default="")


class ReferralInvite(Base):
    """One unique first-time bot entry attributed to a referrer.

    Referral links keep attribution even while the promo is paused. Only rows
    created while the promo is enabled are eligible for the 2 users -> 1 day
    reward. Two eligible rows are marked together with the same reward time in
    the same DB transaction that extends the referrer's access.
    """

    __tablename__ = "referral_invites"
    __table_args__ = (
        UniqueConstraint("referred_user_id", name="uq_referral_invites_referred_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    referred_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    promo_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class AppSetting(Base):
    """Small persistent runtime settings controlled from the admin panel."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
