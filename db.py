import os

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base


def normalize_database_url(url: str) -> str:
    # Railway commonly exposes postgresql://...; async SQLAlchemy needs asyncpg.
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

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _listing_columns(sync_conn) -> set[str]:
    inspector = inspect(sync_conn)
    if "listings" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("listings")}


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Tiny built-in migration from v2.0 so an existing DB can be reused.
        columns = await conn.run_sync(_listing_columns)
        if columns and "category_key" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN category_key VARCHAR(80)"))
        if columns and "posted_text" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN posted_text VARCHAR(100)"))
