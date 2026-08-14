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
    pool_size = max(1, int(os.getenv("DB_POOL_SIZE", "5")))
    max_overflow = max(0, int(os.getenv("DB_MAX_OVERFLOW", "5")))
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
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight additive migrations so existing PostgreSQL databases can be
        # upgraded in place without destructive schema changes.
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
        if columns and "disappeared_at" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN disappeared_at TIMESTAMP"))
        if columns and "view_count" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN view_count INTEGER"))
        if columns and "views_checked_at" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN views_checked_at TIMESTAMP"))

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
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN page_limit INTEGER DEFAULT 100"))
        if settings_columns and "min_views" not in settings_columns:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN min_views INTEGER DEFAULT 0"))

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

    log.info("Database initialized: %s", DATABASE_BACKEND)
