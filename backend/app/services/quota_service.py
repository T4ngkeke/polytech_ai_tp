"""
services/quota_service.py — a student's token limit for one day.

The limit is the user's daily quota plus what teachers granted that day
(QuotaGrant). Every quota check (chat start, each model call) and the usage the
student sees go through daily_limit, so a grant takes effect at once.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import QuotaGrant, UsageStat, User


async def granted(db: AsyncSession, user_id: uuid.UUID, day: date) -> int:
    return int(await db.scalar(select(func.coalesce(func.sum(QuotaGrant.tokens), 0)).where(
        QuotaGrant.user_id == user_id, QuotaGrant.date == day)) or 0)


async def daily_limit(db: AsyncSession, user: User, day: date) -> int:
    return user.daily_token_quota + await granted(db, user.id, day)


async def used(db: AsyncSession, user_id: uuid.UUID, day: date) -> int:
    return int(await db.scalar(select(UsageStat.tokens_used).where(
        UsageStat.user_id == user_id, UsageStat.date == day)) or 0)


@dataclass
class Usage:
    used: int
    limit: int
    granted: int


async def usage(db: AsyncSession, user: User, day: date) -> Usage:
    extra = await granted(db, user.id, day)
    return Usage(used=await used(db, user.id, day), limit=user.daily_token_quota + extra, granted=extra)


async def refill(db: AsyncSession, *, student: User, teacher_id: uuid.UUID, day: date) -> Usage:
    """Give the student a full daily quota again from now on: grant what was
    used today beyond earlier grants. Nothing is granted when the student
    still has a full quota left."""
    current = await usage(db, student, day)
    tokens = current.used - current.granted
    if tokens > 0:
        db.add(QuotaGrant(user_id=student.id, date=day, tokens=tokens, granted_by=teacher_id))
        await db.flush()
        current = Usage(used=current.used, limit=current.limit + tokens, granted=current.granted + tokens)
    return current
