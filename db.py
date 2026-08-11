import os

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_URL = normalize_database_url(
    RAW_DATABASE_URL or "sqlite+aiosqlite:///./kleinanzeigen.db"
)
USING_PERSISTENT_DATABASE = bool(RAW_DATABASE_URL) and not DATABASE_URL.startswith("sqlite")

_IS_SQLITE = DATABASE_URL.startswith("sqlite")
_connect_args = {"timeout": 30} if _IS_SQLITE else {}
engine = create_async_engine(
    DATABASE_URL, echo=False, pool_pre_ping=True, connect_args=_connect_args
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if _IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        # v2.6 can run several parser workers in one process. WAL + busy timeout
        # makes temporary SQLite much more tolerant of concurrent readers/writers.
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


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight migrations so v2.0/v2.1/v2.2 databases can be reused.
        columns = await conn.run_sync(_listing_columns)
        if columns and "category_key" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN category_key VARCHAR(80)"))
        if columns and "posted_text" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN posted_text VARCHAR(100)"))
        if columns and "is_active" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
        if columns and "disappeared_at" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN disappeared_at TIMESTAMP"))

        settings_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "user_settings"))
        if settings_columns and "page_limit" not in settings_columns:
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN page_limit INTEGER DEFAULT 100"))

        scan_columns = await conn.run_sync(lambda sync_conn: _table_columns(sync_conn, "category_scan_state"))
        if scan_columns and "day_seed_capped" not in scan_columns:
            await conn.execute(text("ALTER TABLE category_scan_state ADD COLUMN day_seed_capped BOOLEAN DEFAULT FALSE"))
