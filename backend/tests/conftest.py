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

TEST_PG_URL = os.environ.get("TEST_PG_URL")


@pytest_asyncio.fixture(scope="function")
async def pg_session() -> AsyncGenerator[AsyncSession, None]:
    """Yields an AsyncSession on a live pgvector Postgres, with native types."""
    if not TEST_PG_URL:
        pytest.skip("Set TEST_PG_URL explicitly; no implicit database/port selection")
    from sqlalchemy.engine import make_url
    if not make_url(TEST_PG_URL).database.endswith("_test"):
        pytest.fail("Destructive PostgreSQL fixtures require a database ending in _test")
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
    Class, ClassStudent, Lab,
    Session, SystemConfig, User, UserRole,
)


def make_user(
    role: UserRole = UserRole.student,
    username: str | None = None,
    is_deleted: bool = False,
    quota: int = 50_000,
    weekly_quota: int = 25_000_000,
) -> User:
    """Helper to create a User ORM instance for tests."""
    return User(
        id=uuid.uuid4(),
        username=username or f"user_{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("password1"),
        role=role,
        daily_token_quota=quota,
        weekly_token_quota=weekly_quota,
        is_deleted=is_deleted,
    )


async def add_current_revision(db_session: AsyncSession, document, teacher_id) -> uuid.UUID:
    """Make ``document`` a PDF document whose current revision is a new draft,
    with its source row but no stored files. Returns the revision id. Add its
    passages with add_chunk: content can go into a draft only
    (services/document_snapshot_guards.py), and search without a snapshot
    reads each document's current revision."""
    from backend.app.models import DocumentRevision, DocumentSource
    document.structured = True
    await db_session.flush()
    source = DocumentSource(document_id=document.id, sha256=uuid.uuid4().hex * 2, format="pdf",
                            original_filename=document.filename,
                            storage_key=f"{document.id}/{uuid.uuid4().hex}.pdf", created_by=teacher_id)
    db_session.add(source)
    await db_session.flush()
    revision = DocumentRevision(document_id=document.id, source_version_id=source.id,
                                created_by=teacher_id, processing_state="ready", index_ready=True)
    db_session.add(revision)
    await db_session.flush()
    document.current_revision_id = revision.id
    await db_session.flush()
    return revision.id


async def add_chunk(db_session: AsyncSession, document, *, content: str, page_no: int = 1,
                    chunk_index: int = 0, lab_id="document", **columns):
    """One searchable passage of ``document``'s current revision (see
    add_current_revision), with the page and the block it comes from.
    lab_id: the chunk's lab (default: the document's; None: class-wide)."""
    from sqlalchemy import func, select
    from backend.app.models import DocChunk, DocumentBlock, DocumentPage, DocumentRevision
    revision = await db_session.get(DocumentRevision, document.current_revision_id)
    page = await db_session.scalar(select(DocumentPage).where(
        DocumentPage.revision_id == revision.id, DocumentPage.source_slide_no == page_no))
    if page is None:
        order = await db_session.scalar(select(func.count()).select_from(DocumentPage).where(
            DocumentPage.revision_id == revision.id))
        page = DocumentPage(revision_id=revision.id, source_version_id=revision.source_version_id,
                            order_index=order, source_slide_no=page_no, preview_page_no=page_no)
        db_session.add(page)
        await db_session.flush()
    order = await db_session.scalar(select(func.count()).select_from(DocumentBlock).where(
        DocumentBlock.revision_id == revision.id, DocumentBlock.page_id == page.page_id))
    block = DocumentBlock(revision_id=revision.id, source_version_id=revision.source_version_id,
                          page_id=page.page_id, order_index=order, kind="text", content=content,
                          index_state="searchable")
    db_session.add(block)
    await db_session.flush()
    chunk = DocChunk(document_id=document.id, class_id=document.class_id,
                     lab_id=document.lab_id if lab_id == "document" else lab_id,
                     revision_id=revision.id, block_id=block.block_id, chunk_index=chunk_index,
                     content=content, content_start=0, content_end=len(content),
                     part_index=0, part_count=1, page_no=page_no, **columns)
    db_session.add(chunk)
    await db_session.flush()
    return chunk


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


@pytest.fixture(autouse=True)
def _no_language_call(monkeypatch):
    """The chat names the answer language with one more model call first
    (agent/language.py). Tests that script the model's replies in order do not
    expect it: it is off unless a test sets chat.language_model again."""
    import backend.app.routers.chat as chat_api
    monkeypatch.setattr(chat_api, 'language_model', lambda agent: None)
