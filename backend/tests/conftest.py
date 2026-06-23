"""
conftest.py — shared pytest fixtures for the Edu-LLM test suite.

Two database backends:
  * db_session  — in-memory SQLite (aiosqlite), no external service needed.
  * pg_session  — live pgvector PostgreSQL, for features that need real
                  Postgres semantics (FOR UPDATE SKIP LOCKED, vector ANN).

Column types are dialect-aware in the models themselves (see GUID /
EmbeddingVector in models.py), so no metadata patching is required here — the
same models render native types on Postgres and SQLite-compatible types under
SQLite automatically.
"""

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# ---------------------------------------------------------------------------
# Live pgvector Postgres engine (for SKIP LOCKED + real ANN search)
# ---------------------------------------------------------------------------
# Skipped automatically when no test DB is reachable, so the SQLite suite still
# runs everywhere. Point TEST_PG_URL at any pgvector Postgres to enable.

TEST_PG_URL = os.environ.get(
    "TEST_PG_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/edudb_test",
)


@pytest_asyncio.fixture(scope="function")
async def pg_session() -> AsyncGenerator[AsyncSession, None]:
    """Yields an AsyncSession on a live pgvector Postgres, with native types."""
    # NullPool: don't reuse connections across tests — pytest-asyncio gives each
    # test its own event loop, and a pooled asyncpg connection bound to a closed
    # loop breaks every test after the first.
    engine = create_async_engine(TEST_PG_URL, poolclass=NullPool)

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # DB unreachable → skip, don't fail
        await engine.dispose()
        pytest.skip(f"pgvector Postgres not available at {TEST_PG_URL}: {exc}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
    finally:
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
