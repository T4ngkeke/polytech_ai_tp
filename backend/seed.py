"""
seed.py — Standalone script to seed the DB with initial data for Edu-LLM.

Seeds:
  1. SystemConfig rows (LLM defaults)
  2. Users (admin, teacher, student1, student2)
  3. A default Class with invite code, owned by the teacher
  4. A default Lab inside the class
  5. ClassStudent mappings (student1 + student2 → default class)
"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.auth import hash_password
from backend.app.config import settings
from backend.app.database import Base
from backend.app.models import (
    Class,
    ClassStudent,
    Lab,
    SystemConfig,
    User,
    UserRole,
)

# Recreate an engine with echo=False just for seeding
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


# Default SystemConfig rows (key/value). All model wiring reads these at runtime —
# generation, embeddings, reranking, the agent and vision slots are config,
# not code. Kept as plain dicts so it is unit-testable without a DB.
DEFAULT_SYSTEM_CONFIGS = [
    {"key": "LLM_BASE_URL", "value": settings.LLM_BASE_URL},
    {"key": "LLM_API_KEY", "value": settings.LLM_API_KEY},
    {"key": "LLM_MODEL", "value": settings.LLM_MODEL},
    # embedding model for RAG. bge-m3 = 1024-dim (matches EMBEDDING_DIM).
    # Model id is the provider's full name (Albert/HF style: "BAAI/bge-m3").
    {"key": "EMBEDDING_MODEL", "value": "BAAI/bge-m3"},
    # reranker behind an OpenAI-compatible rerank endpoint (TEI / Infinity / vLLM).
    # Empty URL → retrieval degrades gracefully to fusion-only ordering.
    {"key": "RERANK_URL", "value": ""},
    {"key": "RERANK_MODEL", "value": "Qwen/Qwen3-Reranker-0.6B"},
    # model-routing table. Empty endpoint/key/model values inherit the main
    # LLM (see model_routing.resolve_model_routing), so a fresh install runs as a
    # single engine and a split is purely opt-in by the admin.
    {"key": "EMBEDDING_URL", "value": ""},        # empty → LLM_BASE_URL
    {"key": "EMBEDDING_API_KEY", "value": ""},    # empty → LLM_API_KEY
    {"key": "RERANK_API_KEY", "value": ""},       # empty → LLM_API_KEY
    # weighted-token quota: billed = prompt*alpha + completion*beta.
    {"key": "TOKEN_ALPHA", "value": "0.2"},       # prefill is cheaper than decode
    {"key": "TOKEN_BETA", "value": "1.0"},
    # The agent's reasoning model: point at the BIG model — quality over
    # latency. Empty → main LLM.
    {"key": "AGENT_MODEL", "value": ""},
    {"key": "AGENT_BASE_URL", "value": ""},
    {"key": "AGENT_API_KEY", "value": ""},
    # estimated-token budget for chat history (OOM protection; chars//3).
    {"key": "CONTEXT_MAX_TOKENS", "value": "8000"},
    # VLM slot — garbled-formula transcription (empty model = off).
    {"key": "VLM_BASE_URL", "value": ""},
    {"key": "VLM_API_KEY", "value": ""},
    {"key": "VLM_MODEL", "value": ""},
]


async def seed():
    print("Connecting to DB and creating tables if needed...")
    async with engine.begin() as conn:
        # pgvector must exist before create_all builds vector() columns.
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # ── 1. SystemConfig (LLM defaults) ───────────────────────
        print("Inserting SystemConfig rows (LLM defaults)...")
        # Use the env-injected base URL (compose sets host.docker.internal so the
        # backend container can reach the host's model server). Falls back to the
        # config default (localhost) when running natively.
        db.add_all(SystemConfig(**c) for c in DEFAULT_SYSTEM_CONFIGS)

        # ── 2. Users ─────────────────────────────────────────────
        print("Inserting 4 users (admin, teacher, student1, student2)...")
        admin = User(
            username="admin",
            hashed_password=hash_password("admin123"),
            role=UserRole.admin,
            daily_token_quota=100_000,
        )
        teacher = User(
            username="teacher",
            hashed_password=hash_password("teacher123"),
            role=UserRole.teacher,
            daily_token_quota=100_000,
        )
        student1 = User(
            username="student1",
            hashed_password=hash_password("student123"),
            role=UserRole.student,
            daily_token_quota=50_000,
        )
        student2 = User(
            username="student2",
            hashed_password=hash_password("student123"),
            role=UserRole.student,
            daily_token_quota=50_000,
        )
        db.add_all([admin, teacher, student1, student2])
        await db.flush()  # Populate IDs

        # ── 3. Default Class ─────────────────────────────────────
        print("Creating default class with invite code...")
        invite_code = "ABC123"  # Deterministic for demo reproducibility
        default_class = Class(
            name="Default Class",
            teacher_id=teacher.id,
            invite_code=invite_code,
        )
        db.add(default_class)
        await db.flush()

        # ── 4. Default Lab ───────────────────────────────────────
        print("Creating default lab...")
        default_lab = Lab(
            class_id=default_class.id,
            name="Lab 1 — General",
        )
        db.add(default_lab)
        await db.flush()

        # ── 5. Map students to class ─────────────────────────────
        print("Mapping student1 and student2 to the default class...")
        membership1 = ClassStudent(
            class_id=default_class.id,
            student_id=student1.id,
        )
        membership2 = ClassStudent(
            class_id=default_class.id,
            student_id=student2.id,
        )
        db.add_all([membership1, membership2])

        try:
            await db.commit()
            print("\n✅ Database seeded successfully!")
            print("─" * 50)
            print("Credentials:")
            print("  admin    / admin123")
            print("  teacher  / teacher123")
            print("  student1 / student123")
            print("  student2 / student123")
            print("─" * 50)
            print(f"Default Class invite code: {invite_code}")
            print(f"Default Lab: Lab 1 — General")
            print("─" * 50)
            print(f"LLM Config: {settings.LLM_MODEL} @ {settings.LLM_BASE_URL}")
        except Exception as e:
            print(f"\n❌ Failed to seed database. Maybe data already exists?\nError: {e}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
