import asyncio
import logging
import os
from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base

log = logging.getLogger(__name__)


from db_url import normalize_database_url


RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_IS_RAILWAY = bool(
    os.getenv("RAILWAY_ENVIRONMENT")
    or os.getenv("RAILWAY_ENVIRONMENT_ID")
    or os.getenv("RAILWAY_PROJECT_ID")
    or os.getenv("RAILWAY_SERVICE_ID")
)

# v3.3.0: PostgreSQL is mandatory on Railway. Local SQLite remains available only
# as a zero-setup development/test fallback so the included unit tests still run.
if _IS_RAILWAY and not RAW_DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is required on Railway in v3.3.0. Add a PostgreSQL service "
        "and set DATABASE_URL=${{Postgres.DATABASE_URL}} in the parser service Variables."
    )

DATABASE_URL = normalize_database_url(
    RAW_DATABASE_URL or "sqlite+aiosqlite:///./kleinanzeigen.dev.db"
)
_IS_SQLITE = DATABASE_URL.startswith("sqlite")
_IS_POSTGRES = DATABASE_URL.startswith("postgresql+asyncpg://")

if _IS_RAILWAY and not _IS_POSTGRES:
    raise RuntimeError(
        "v3.3.0 requires PostgreSQL on Railway. DATABASE_URL must point to the Railway PostgreSQL service."
    )
if not _IS_SQLITE and not _IS_POSTGRES:
    raise RuntimeError("Unsupported DATABASE_URL. Use PostgreSQL (postgresql://...) or local SQLite for development.")

USING_PERSISTENT_DATABASE = _IS_POSTGRES
DATABASE_BACKEND = "PostgreSQL" if _IS_POSTGRES else "SQLite (local development)"

if _IS_SQLITE:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        connect_args={"timeout": 30},
    )
else:
    distributed_mode = os.getenv("DISTRIBUTED_WORKERS", "0").strip().lower() in {"1", "true", "yes", "on"}
    # Each Railway replica owns its own SQLAlchemy pool. With 5 parser workers +
    # bot + views worker, the old 5+5 default could reserve far too many PostgreSQL
    # connections. Distributed mode therefore uses a smaller per-process pool.
    default_pool = "3" if distributed_mode else "5"
    default_overflow = "2" if distributed_mode else "5"
    pool_size = max(1, int(os.getenv("DB_POOL_SIZE", default_pool)))
    max_overflow = max(0, int(os.getenv("DB_MAX_OVERFLOW", default_overflow)))
    pool_timeout = max(5, int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30")))
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=1800,
    )

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if _IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


def _listing_columns(sync_conn) -> set[str]:
    return _table_columns(sync_conn, "listings")


def _table_columns(sync_conn, table_name: str) -> set[str]:
    inspector = inspect(sync_conn)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


async def wait_for_database() -> None:
    """Wait for Railway PostgreSQL to accept connections during a fresh deploy."""
    attempts = max(1, int(os.getenv("DB_CONNECT_ATTEMPTS", "15")))
    delay = max(0.5, float(os.getenv("DB_CONNECT_RETRY_SECONDS", "2")))
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if _IS_POSTGRES:
                log.info("PostgreSQL connection ready")
            return
        except Exception as exc:  # pragma: no cover - depends on external DB state
            last_error = exc
            if attempt >= attempts:
                break
            log.warning("Database not ready (%s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(delay)

    raise RuntimeError(f"Database is unavailable after {attempts} attempts: {last_error}") from last_error


async def init_db() -> None:
    await wait_for_database()
    async with engine.begin() as conn:
        # v4.11.7: this table is created explicitly with IF NOT EXISTS before the
        # metadata pass. Railway starts parser/date/page/view services close together;
        # the explicit PostgreSQL DDL avoids a create_all check/create race on the new
        # funnel table during a multi-service deploy. SQLite keeps the normal metadata
        # creation path used by local tests.
        if _IS_POSTGRES:
            await conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS free_radar_events (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    event_type VARCHAR(32) NOT NULL,
                    mode VARCHAR(24) NOT NULL DEFAULT '',
                    feature VARCHAR(40) NOT NULL DEFAULT '',
                    product_id INTEGER,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            ))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_free_radar_events_user_id ON free_radar_events (user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_free_radar_events_event_type ON free_radar_events (event_type)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_free_radar_events_mode ON free_radar_events (mode)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_free_radar_events_feature ON free_radar_events (feature)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_free_radar_events_product_id ON free_radar_events (product_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_free_radar_events_created_at ON free_radar_events (created_at)"))
            # v4.13.0: referral attribution is a small standalone table. Create it
            # explicitly before metadata.create_all so multiple Railway services
            # starting together cannot race on first deployment.
            await conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS referral_invites (
                    id SERIAL PRIMARY KEY,
                    referrer_user_id BIGINT NOT NULL,
                    referred_user_id BIGINT NOT NULL UNIQUE,
                    promo_eligible BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    rewarded_at TIMESTAMP
                )
                """
            ))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_invites_referrer_user_id ON referral_invites (referrer_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_invites_referred_user_id ON referral_invites (referred_user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_invites_promo_eligible ON referral_invites (promo_eligible)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_invites_created_at ON referral_invites (created_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referral_invites_rewarded_at ON referral_invites (rewarded_at)"))
            # v4.15.2: sticky registry for ads whose demand is non-organic.
            await conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS listing_integrity (
                    external_id VARCHAR(64) PRIMARY KEY,
                    is_promoted BOOLEAN NOT NULL DEFAULT FALSE,
                    is_price_reduced BOOLEAN NOT NULL DEFAULT FALSE,
                    first_detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            ))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listing_integrity_is_promoted ON listing_integrity (is_promoted)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listing_integrity_is_price_reduced ON listing_integrity (is_price_reduced)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listing_integrity_first_detected_at ON listing_integrity (first_detected_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listing_integrity_last_detected_at ON listing_integrity (last_detected_at)"))

            # v4.14.0: DT Radar Lifecycle / Fast Sold. PostgreSQL itself is the
            # durable queue so a dedicated Lifecycle Worker needs no Redis and a
            # parser restart cannot lose pending availability checks.
            await conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS radar_lifecycle_watches (
                    id SERIAL PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    external_id VARCHAR(64) NOT NULL UNIQUE,
                    category_key VARCHAR(80) NOT NULL DEFAULT '',
                    title VARCHAR(500) NOT NULL DEFAULT '',
                    url VARCHAR(1200) NOT NULL DEFAULT '',
                    first_seen_at TIMESTAMP NOT NULL,
                    radar_started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(24) NOT NULL DEFAULT 'watching',
                    tier VARCHAR(8) NOT NULL DEFAULT 'B',
                    score INTEGER NOT NULL DEFAULT 0,
                    peak_score INTEGER NOT NULL DEFAULT 0,
                    last_views INTEGER,
                    last_price_eur INTEGER,
                    check_step INTEGER NOT NULL DEFAULT 0,
                    checks INTEGER NOT NULL DEFAULT 0,
                    consecutive_missing INTEGER NOT NULL DEFAULT 0,
                    next_check_at TIMESTAMP,
                    last_checked_at TIMESTAMP,
                    first_missing_at TIMESTAMP,
                    disappeared_at TIMESTAMP,
                    confirmed_at TIMESTAMP,
                    lifetime_seconds INTEGER,
                    last_result VARCHAR(32) NOT NULL DEFAULT '',
                    last_error VARCHAR(1000),
                    lease_owner VARCHAR(120) NOT NULL DEFAULT '',
                    lease_until TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            ))
            for index_sql in (
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_product_id ON radar_lifecycle_watches (product_id)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_external_id ON radar_lifecycle_watches (external_id)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_category_key ON radar_lifecycle_watches (category_key)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_first_seen_at ON radar_lifecycle_watches (first_seen_at)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_status ON radar_lifecycle_watches (status)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_score ON radar_lifecycle_watches (score)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_next_check_at ON radar_lifecycle_watches (next_check_at)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_disappeared_at ON radar_lifecycle_watches (disappeared_at)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_lifetime_seconds ON radar_lifecycle_watches (lifetime_seconds)",
                "CREATE INDEX IF NOT EXISTS ix_radar_lifecycle_watches_lease_until ON radar_lifecycle_watches (lease_until)",
            ):
                await conn.execute(text(index_sql))
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight additive migrations so existing PostgreSQL databases can be
        # upgraded in place without destructive schema changes.

        # v3.4.2: Telegram user IDs are 64-bit values. Newer accounts can exceed
        # PostgreSQL INTEGER (2,147,483,647), which previously caused /start to
        # fail before the user was even visible in the admin panel. Promote every
        # persisted Telegram user_id column to BIGINT in-place. Existing values,
        # indexes, unique constraints and primary keys are preserved by PostgreSQL.
        if _IS_POSTGRES:
            telegram_user_id_columns = (
                ("bot_users", "user_id"),
                ("selected_categories", "user_id"),
                ("user_settings", "user_id"),
                ("parser_runs", "user_id"),
                ("user_scans", "user_id"),
                ("subscription_payments", "user_id"),
            )
            for table_name, column_name in telegram_user_id_columns:
                cols = await conn.run_sync(lambda sync_conn, t=table_name: _table_columns(sync_conn, t))
                if column_name not in cols:
                    continue
                data_type = (await conn.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = :table_name AND column_name = :column_name"
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )).scalar_one_or_none()
                if data_type == "integer":
                    await conn.execute(text(
                        f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" TYPE BIGINT USING "{column_name}"::BIGINT'
                    ))
                    log.info("Migrated %s.%s to BIGINT", table_name, column_name)
        columns = await conn.run_sync(_listing_columns)
        if columns and "category_key" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN category_key VARCHAR(80)"))
        if columns and "posted_text" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN posted_text VARCHAR(100)"))
        if columns and "posted_date_msk" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN posted_date_msk VARCHAR(10)"))
        if columns and "is_active" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
        if columns and "is_promoted" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN is_promoted BOOLEAN DEFAULT FALSE"))
        if columns and "is_price_reduced" not in columns:
            if _IS_POSTGRES:
                await conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_price_reduced BOOLEAN DEFAULT FALSE"))
            else:
                await conn.execute(text("ALTER TABLE listings ADD COLUMN is_price_reduced BOOLEAN DEFAULT FALSE"))
        if columns and "disappeared_at" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN disappeared_at TIMESTAMP"))
        if columns and "view_count" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN view_count INTEGER"))
        if columns and "views_checked_at" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN views_checked_at TIMESTAMP"))
        if _IS_POSTGRES:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_is_price_reduced ON listings (is_price_reduced)"))

        # v4.15.3 Strict Organic Radar Gate. Existing Radar families are not
        # trusted merely because older search-card parsing called them clean.
        # NULL quarantines legacy families until a live detail-page check passes.
        radar_product_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "radar_products"))
        if radar_product_columns and "organic_verified_at" not in radar_product_columns:
            if _IS_POSTGRES:
                await conn.execute(text("ALTER TABLE radar_products ADD COLUMN IF NOT EXISTS organic_verified_at TIMESTAMP"))
            else:
                await conn.execute(text("ALTER TABLE radar_products ADD COLUMN organic_verified_at TIMESTAMP"))
        if _IS_POSTGRES:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_radar_products_organic_verified_at ON radar_products (organic_verified_at)"))

        identity_columns = {
            "identity_key": "VARCHAR(500)",
            "identity_label": "VARCHAR(500)",
            "identity_brand": "VARCHAR(80)",
            "identity_model": "VARCHAR(180)",
            "identity_variant": "VARCHAR(180)",
            "identity_product_type": "VARCHAR(80)",
            "identity_storage_gb": "INTEGER",
            "identity_ram_gb": "INTEGER",
            "identity_specs": "VARCHAR(300)",
            "identity_confidence": "INTEGER",
        }
        for column_name, sql_type in identity_columns.items():
            if columns and column_name not in columns:
                await conn.execute(text(f"ALTER TABLE listings ADD COLUMN {column_name} {sql_type}"))

        settings_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "user_settings"))
        if settings_columns and "page_limit" not in settings_columns:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN page_limit INTEGER DEFAULT 50"))
        if settings_columns and "min_views" not in settings_columns:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN min_views INTEGER DEFAULT 0"))
        if settings_columns and "auto_observations" not in settings_columns:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN auto_observations BOOLEAN DEFAULT FALSE"))

        scan_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "category_scan_state"))
        if scan_columns and "day_seed_capped" not in scan_columns:
            await conn.execute(text("ALTER TABLE category_scan_state ADD COLUMN day_seed_capped BOOLEAN DEFAULT FALSE"))
        if scan_columns and "target_date" not in scan_columns:
            await conn.execute(text("ALTER TABLE category_scan_state ADD COLUMN target_date VARCHAR(10) DEFAULT ''"))

        scan_view_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "scan_view_history"))
        if scan_view_columns and "target_hours" not in scan_view_columns:
            await conn.execute(text("ALTER TABLE scan_view_history ADD COLUMN target_hours INTEGER"))

        parser_run_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "parser_runs"))
        parser_quality_columns = {
            "cards_seen": "INTEGER DEFAULT 0",
            "listings_parsed": "INTEGER DEFAULT 0",
            "missing_date_count": "INTEGER DEFAULT 0",
            "missing_price_count": "INTEGER DEFAULT 0",
            "promoted_filtered": "INTEGER DEFAULT 0",
            "duplicate_count": "INTEGER DEFAULT 0",
            "invalid_pages": "INTEGER DEFAULT 0",
            "repeated_pages": "INTEGER DEFAULT 0",
            "low_quality_pages": "INTEGER DEFAULT 0",
            "view_failures": "INTEGER DEFAULT 0",
            "quality_score": "INTEGER DEFAULT 0",
        }
        for column_name, sql_type in parser_quality_columns.items():
            if parser_run_columns and column_name not in parser_run_columns:
                await conn.execute(text(f"ALTER TABLE parser_runs ADD COLUMN {column_name} {sql_type}"))

        user_scan_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "user_scans"))
        if user_scan_columns and "target_date" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN target_date VARCHAR(10) DEFAULT ''"))
        if user_scan_columns and "price_filter" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN price_filter VARCHAR(32) DEFAULT 'any'"))
        if user_scan_columns and "target_complete" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN target_complete BOOLEAN DEFAULT FALSE"))
        if user_scan_columns and "scan_note" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN scan_note VARCHAR(500) DEFAULT ''"))
        if user_scan_columns and "quality_score" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN quality_score INTEGER DEFAULT 0"))
        if user_scan_columns and "quality_note" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN quality_note VARCHAR(500) DEFAULT ''"))
        if user_scan_columns and "archived_at" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN archived_at TIMESTAMP"))
        if user_scan_columns and "chat_id" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN chat_id BIGINT"))
        if user_scan_columns and "status_message_id" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN status_message_id BIGINT"))
        if user_scan_columns and "resumed_count" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN resumed_count INTEGER DEFAULT 0"))
        if user_scan_columns and "retry_count" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN retry_count INTEGER DEFAULT 0"))
        if user_scan_columns and "last_error" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN last_error VARCHAR(1000)"))
        if user_scan_columns and "incomplete_category_keys" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN incomplete_category_keys TEXT DEFAULT ''"))
        if user_scan_columns and "is_trial" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN is_trial BOOLEAN DEFAULT FALSE"))
        if user_scan_columns and "trial_credit_refunded" not in user_scan_columns:
            await conn.execute(text("ALTER TABLE user_scans ADD COLUMN trial_credit_refunded BOOLEAN DEFAULT FALSE"))

        bot_user_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "bot_users"))
        if bot_user_columns and "onboarding_completed" not in bot_user_columns:
            await conn.execute(text("ALTER TABLE bot_users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE"))
            # Existing v3.2.x users already know the product; onboarding is only for
            # new users. They can still replay it from /help.
            await conn.execute(text("UPDATE bot_users SET onboarding_completed = TRUE"))
        if bot_user_columns and "expiry_warning_sent_for" not in bot_user_columns:
            await conn.execute(text("ALTER TABLE bot_users ADD COLUMN expiry_warning_sent_for TIMESTAMP"))
        if bot_user_columns and "expiry_expired_sent_for" not in bot_user_columns:
            await conn.execute(text("ALTER TABLE bot_users ADD COLUMN expiry_expired_sent_for TIMESTAMP"))
        if bot_user_columns and "trial_scans_used" not in bot_user_columns:
            await conn.execute(text("ALTER TABLE bot_users ADD COLUMN trial_scans_used INTEGER DEFAULT 0"))

        # v4.6: additive Product Opportunity Engine fields. Existing AI history is
        # preserved; old candidates receive neutral defaults and new runs populate them.
        ai_candidate_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "ai_early_winner_candidates"))
        ai_opportunity_columns = {
            "cohort_key": "VARCHAR(600) DEFAULT ''",
            "opportunity_type": "VARCHAR(32) DEFAULT 'spark'",
            "saturation_score": "INTEGER DEFAULT 0",
            "supply_percentile": "FLOAT DEFAULT 0",
            "supply_growth_ratio": "FLOAT DEFAULT 1",
            "demand_growth_ratio": "FLOAT DEFAULT 1",
            "demand_supply_ratio": "FLOAT DEFAULT 1",
            "repeatability": "FLOAT DEFAULT 0",
        }
        for column_name, sql_type in ai_opportunity_columns.items():
            if not ai_candidate_columns or column_name in ai_candidate_columns:
                continue
            if _IS_POSTGRES:
                # Main Bot and AI Worker may start together on Railway. IF NOT EXISTS
                # makes the additive v4.6 migration safe under that startup race.
                await conn.execute(text(
                    f"ALTER TABLE ai_early_winner_candidates ADD COLUMN IF NOT EXISTS {column_name} {sql_type}"
                ))
            else:
                await conn.execute(text(f"ALTER TABLE ai_early_winner_candidates ADD COLUMN {column_name} {sql_type}"))
        if _IS_POSTGRES and ai_candidate_columns:
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_ai_ew_candidate_cohort_key "
                "ON ai_early_winner_candidates (cohort_key)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_ai_ew_candidate_opportunity_type "
                "ON ai_early_winner_candidates (opportunity_type)"
            ))

    log.info("Database initialized: %s", DATABASE_BACKEND)
