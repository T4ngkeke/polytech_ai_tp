"""
main.py — FastAPI application entry point for Edu-LLM v7 Agentic Class-Lab Architecture.

Startup sequence
----------------
1. Create database tables (create_all) if they don't exist yet.
2. Register all routers under their respective prefixes.
3. Attach CORS middleware for frontend dev server (localhost:5173).

Running locally
---------------
    uvicorn backend.app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.database import Base, engine
from backend.app.routers import admin, auth, chat, student, teacher, document_versions, document_reader

# ---------------------------------------------------------------------------
# Lifespan: create tables on startup (dev convenience — use Alembic in prod)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    async with engine.begin() as conn:
        # Import models so Base.metadata is populated before create_all
        import backend.app.models  # noqa: F401

        # [v7] pgvector must exist before create_all builds vector() columns.
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # create_all cannot add columns to an existing installation. Fail before
        # partially creating snapshot tables against an unmigrated legacy schema.
        from sqlalchemy import inspect
        # Columns each explicit migration adds to a table that already exists.
        # The ORM selects them, so a missing one breaks even the legacy path.
        migrated_columns = {"documents": "structured", "ingestion_jobs": "task_kind",
                            "document_reviews": "revision_id", "document_blocks": "structure"}
        def needs_migration(sync_conn):
            inspector = inspect(sync_conn)
            return any(inspector.has_table(table) and column not in {
                col["name"] for col in inspector.get_columns(table)}
                for table, column in migrated_columns.items())
        if await conn.run_sync(needs_migration):
            raise RuntimeError("Apply scripts/migrate_document_snapshots.py explicitly before starting this version")
        await conn.run_sync(Base.metadata.create_all)
        from backend.app.config import settings
        if conn.dialect.name == "postgresql" and (
                settings.STRUCTURED_INGEST_ENABLED or settings.AGENTIC_SEARCH_ENABLED):
            from sqlalchemy import text
            ledger = await conn.scalar(text("SELECT to_regclass('document_schema_migrations')"))
            if ledger is None:
                raise RuntimeError("Structured writes require the explicit M1 migration (including DB guards)")
            from backend.app.schema_versions import (
                REQUIRED_SCHEMA_VERSIONS, REQUIRED_STRUCTURED_SCHEMA_VERSIONS,
            )
            applied = set((await conn.scalars(text("SELECT version FROM document_schema_migrations"))).all())
            required = (REQUIRED_SCHEMA_VERSIONS if settings.AGENTIC_SEARCH_ENABLED
                        else REQUIRED_STRUCTURED_SCHEMA_VERSIONS)
            missing = [version for version in required if version not in applied]
            if missing:
                raise RuntimeError(f"Structured migrations not applied: {', '.join(missing)}")
    yield
    # Teardown: nothing to clean up — connection pool disposes automatically.


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Edu-LLM v7 Agentic Class-Lab Architecture",
    description=(
        "Educational LLM platform with 3-tier RBAC, hierarchical Class-Lab structure, "
        "three-tier rule injection, dynamic LLM config, and invite-code join flow."
    ),
    version="7.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS (allow React dev server on port 5173)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(teacher.router)
app.include_router(document_versions.router)
app.include_router(document_reader.teacher_router)
app.include_router(document_reader.student_router)
app.include_router(student.router)
app.include_router(chat.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health", tags=["meta"])
async def health():
    """Liveness probe — returns 200 OK when the server is up."""
    return {"status": "ok", "version": "5.0.0"}
