"""
services/analytics_service.py — Hierarchical token usage analytics.

Provides aggregated analytics at class→lab→student granularity.
Both Teacher and Admin routers call these functions — Teacher passes
class_id with ownership already verified; Admin passes it directly.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Lab, Message, SenderType, Session, UsageStat, User


@dataclass
class StudentUsage:
    user_id: uuid.UUID
    username: str
    tokens_used: int
    request_count: int


@dataclass
class LabUsage:
    lab_id: uuid.UUID
    lab_name: str
    tokens: int
    requests: int
    students: list[StudentUsage] = field(default_factory=list)


@dataclass
class ClassAnalytics:
    class_id: uuid.UUID
    class_name: str
    total_tokens: int
    total_requests: int
    labs: list[LabUsage] = field(default_factory=list)


@dataclass
class GlobalAnalytics:
    total_tokens: int
    total_requests: int
    daily_breakdown: list[dict] = field(default_factory=list)


async def get_class_analytics(db: AsyncSession, class_id: uuid.UUID, class_name: str) -> ClassAnalytics:
    """
    Build a full hierarchical analytics report for a class.
    Returns: ClassAnalytics with per-lab and per-student breakdown.
    """
    # Get all labs for this class
    labs_result = await db.execute(
        select(Lab).where(Lab.class_id == class_id, Lab.is_deleted.is_(False))
    )
    labs = labs_result.scalars().all()

    class_total_tokens = 0
    class_total_requests = 0
    lab_usages: list[LabUsage] = []

    for lab in labs:
        # Aggregate per-student usage for this lab. A "request" is one exchange =
        # one llm reply, so count only llm messages (counting all Message rows
        # would double it by including the paired user message), consistent with
        # UsageStat.request_count used by the global dashboard.
        stats_result = await db.execute(
            select(
                Session.user_id,
                func.coalesce(func.sum(Message.total_tokens), 0),
                func.count(Message.id).filter(Message.sender == SenderType.llm),
            )
            .join(Message, Message.session_id == Session.id)
            .where(Session.lab_id == lab.id)
            .group_by(Session.user_id)
        )
        user_stats = stats_result.all()

        lab_tokens = sum(r[1] for r in user_stats)
        lab_requests = sum(r[2] for r in user_stats)
        class_total_tokens += lab_tokens
        class_total_requests += lab_requests

        students: list[StudentUsage] = []
        for user_id, tokens, req_count in user_stats:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if user:
                students.append(StudentUsage(
                    user_id=user.id,
                    username=user.username,
                    tokens_used=tokens,
                    request_count=req_count,
                ))

        lab_usages.append(LabUsage(
            lab_id=lab.id,
            lab_name=lab.name,
            tokens=lab_tokens,
            requests=lab_requests,
            students=students,
        ))

    return ClassAnalytics(
        class_id=class_id,
        class_name=class_name,
        total_tokens=class_total_tokens,
        total_requests=class_total_requests,
        labs=lab_usages,
    )


async def get_global_analytics(db: AsyncSession) -> GlobalAnalytics:
    """Platform-wide token usage summary with 30-day daily breakdown."""
    totals_result = await db.execute(
        select(
            func.coalesce(func.sum(UsageStat.tokens_used), 0),
            func.coalesce(func.sum(UsageStat.request_count), 0),
        )
    )
    row = totals_result.one()
    total_tokens = row[0]
    total_requests = row[1]

    daily_result = await db.execute(
        select(
            UsageStat.date,
            func.sum(UsageStat.tokens_used),
            func.sum(UsageStat.request_count),
        )
        .group_by(UsageStat.date)
        .order_by(UsageStat.date.desc())
        .limit(30)
    )
    daily_breakdown = [
        {"date": str(r[0]), "tokens": r[1], "requests": r[2]}
        for r in daily_result.all()
    ]

    return GlobalAnalytics(
        total_tokens=total_tokens,
        total_requests=total_requests,
        daily_breakdown=daily_breakdown,
    )
