"""
main.py — FastAPI application entry point for Edu-LLM v3 Lean MVP.

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
from backend.app.routers import admin, auth, chat, student, teacher

# ---------------------------------------------------------------------------
# Lifespan: create tables on startup (dev convenience — use Alembic in prod)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    async with engine.begin() as conn:
        # Import models so Base.metadata is populated before create_all
        import backend.app.models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)
    yield
    # Teardown: nothing to clean up — connection pool disposes automatically.


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Edu-LLM v3 Lean MVP",
    description=(
        "Educational LLM platform with 3-tier RBAC, teacher-rule injection, "
        "direct SSE streaming, and DB-based rate limiting."
    ),
    version="3.0.0",
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
app.include_router(student.router)
app.include_router(chat.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health", tags=["meta"])
async def health():
    """Liveness probe — returns 200 OK when the server is up."""
    return {"status": "ok", "version": "3.0.0"}
