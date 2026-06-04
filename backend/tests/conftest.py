"""
conftest.py — shared pytest fixtures for the Edu-LLM v5 test suite.

Uses an in-memory SQLite database (aiosqlite) so tests run without a live
PostgreSQL instance.  SQLite does not support the PostgreSQL-specific UUID
and Enum column types, so we override them before table creation.
"""

import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.database import Base, get_db
from backend.app.main import app
import backend.app.models as models  # noqa: F401 — ensures models are registered on Base.metadata


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

    # Patch PostgreSQL JSON → TEXT for SQLite (if any remain)
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


# ---------------------------------------------------------------------------
# Helpers: create authenticated AsyncClient instances
# ---------------------------------------------------------------------------

from backend.app.auth import create_access_token, hash_password
from backend.app.models import (
    Class, ClassStudent, Lab, Rule, RuleLevel,
    Session, SystemConfig, User, UserRole,
)


def make_user(
    role: UserRole = UserRole.student,
    username: str | None = None,
    is_deleted: bool = False,
    quota: int = 50_000,
) -> User:
    """Helper to create a User ORM instance for tests."""
    return User(
        id=uuid.uuid4(),
        username=username or f"user_{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("password1"),
        role=role,
        daily_token_quota=quota,
        is_deleted=is_deleted,
    )


async def make_client(
    db_session: AsyncSession,
    user: User,
) -> AsyncClient:
    """Create an authenticated AsyncClient for the given user."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=user.id)
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
