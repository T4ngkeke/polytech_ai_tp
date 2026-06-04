"""
seed.py — Standalone script to seed the DB with initial data for Edu-LLM v5.

Seeds:
  1. SystemConfig rows (LLM defaults for Ollama)
  2. Users (admin, teacher, student1, student2)
  3. A default Class with invite code, owned by the teacher
  4. A default Lab inside the class
  5. ClassStudent mappings (student1 + student2 → default class)
"""

import asyncio
import secrets

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


async def seed():
    print("Connecting to DB and creating tables if needed...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # ── 1. SystemConfig (LLM defaults) ───────────────────────
        print("Inserting SystemConfig rows (Ollama defaults)...")
        configs = [
            SystemConfig(key="LLM_BASE_URL", value="http://localhost:11434/v1"),
            SystemConfig(key="LLM_API_KEY", value="ollama"),
            SystemConfig(key="LLM_MODEL", value="qwen3.5:0.8b"),
        ]
        db.add_all(configs)

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
            print("LLM Config: Ollama @ http://localhost:11434/v1")
        except Exception as e:
            print(f"\n❌ Failed to seed database. Maybe data already exists?\nError: {e}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
