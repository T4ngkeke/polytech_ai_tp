"""
seed.py — Standalone script to seed the DB with initial users.
"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.auth import hash_password
from backend.app.config import settings
from backend.app.database import Base
from backend.app.models import User, UserRole

# Recreate an engine with echo=False just for seeding
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def seed():
    print("Connecting to DB and creating tables if needed...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        users = [
            User(
                username="admin",
                hashed_password=hash_password("admin123"),
                role=UserRole.admin,
                daily_token_quota=100_000,
            ),
            User(
                username="teacher",
                hashed_password=hash_password("teacher123"),
                role=UserRole.teacher,
                daily_token_quota=100_000,
            ),
            User(
                username="student1",
                hashed_password=hash_password("student123"),
                role=UserRole.student,
                daily_token_quota=50_000,
            ),
            User(
                username="student2",
                hashed_password=hash_password("student123"),
                role=UserRole.student,
                daily_token_quota=50_000,
            ),
        ]

        print("Inserting 4 users (admin, teacher, student1, student2)...")
        # Since this is a simple script, we don't check for duplicates.
        # Run it only on an empty database.
        db.add_all(users)
        try:
            await db.commit()
            print("\nDatabase seeded successfully!")
            print("Credentials:")
            print("  admin    / admin123")
            print("  teacher  / teacher123")
            print("  student1 / student123")
            print("  student2 / student123")
        except Exception as e:
            print(f"\nFailed to seed database. Maybe they already exist?\nError: {e}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
