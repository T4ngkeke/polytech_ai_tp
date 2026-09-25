"""Initialize an empty beta database without demo accounts or fixed passwords."""

import asyncio
import os

from sqlalchemy import func, select

from backend.app.auth import hash_password
from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import SystemConfig, User, UserRole


def required_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 20 or value in {"admin123", "teacher123", "student123"}:
        raise SystemExit(f"{name} must be a unique password of at least 20 characters")
    return value


async def bootstrap() -> None:
    if settings.JWT_SECRET == "change-me-in-production":
        raise SystemExit("Set a unique JWT_SECRET before bootstrapping")

    admin_name = os.environ.get("BETA_ADMIN_USERNAME", "beta_admin")
    teacher_name = os.environ.get("BETA_TEACHER_USERNAME", "beta_teacher")
    if not admin_name or not teacher_name or admin_name == teacher_name:
        raise SystemExit("Beta admin and teacher must have distinct usernames")
    admin_password = required_secret("BETA_ADMIN_PASSWORD")
    teacher_password = required_secret("BETA_TEACHER_PASSWORD")

    async with AsyncSessionLocal() as db:
        count = await db.scalar(select(func.count()).select_from(User))
        if count:
            raise SystemExit("Database already contains users; bootstrap refused")

        db.add_all([
            User(username=admin_name, hashed_password=hash_password(admin_password),
                 role=UserRole.admin, daily_token_quota=100_000),
            User(username=teacher_name, hashed_password=hash_password(teacher_password),
                 role=UserRole.teacher, daily_token_quota=100_000),
            SystemConfig(key="LLM_BASE_URL", value=settings.LLM_BASE_URL),
            SystemConfig(key="LLM_API_KEY", value=settings.LLM_API_KEY),
            SystemConfig(key="LLM_MODEL", value=settings.LLM_MODEL),
        ])
        await db.commit()
    print("Beta admin, teacher and model configuration created")


if __name__ == "__main__":
    asyncio.run(bootstrap())
