"""
conftest.py — shared pytest fixtures for the auth test suite.

Uses an in-memory SQLite database (aiosqlite) so tests run without a live
PostgreSQL instance.  SQLite does not support the PostgreSQL-specific UUID
and Enum column types, so we override them before table creation.
"""

import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import String, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.database import Base
import backend.app.models  # noqa: F401 — ensures models are registered on Base.metadata


# ---------------------------------------------------------------------------
# In-memory async SQLite engine (no PostgreSQL needed for unit tests)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yields a fresh AsyncSession backed by an in-memory SQLite database.
    Tables are created before each test and dropped after.
    """
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})

    # SQLite doesn't know PostgreSQL's UUID type — map it to String(36)
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy import TypeDecorator, String as SAString

    class UUIDString(TypeDecorator):
        impl = SAString(36)
        cache_ok = True

        def process_bind_param(self, value, dialect):
            return str(value) if value is not None else None

        def process_result_value(self, value, dialect):
            return uuid.UUID(value) if value is not None else None

    # Patch all UUID columns in metadata to use UUIDString
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_UUID):
                col.type = UUIDString()

    # Patch PostgreSQL JSON → TEXT for SQLite
    from sqlalchemy.dialects.postgresql import JSON as PG_JSON
    from sqlalchemy import Text

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSON):
                col.type = Text()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
