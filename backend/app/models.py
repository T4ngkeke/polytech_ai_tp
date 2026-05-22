"""
models.py — SQLAlchemy ORM models for Edu-LLM v3 Lean MVP.

Tables
------
- users           : platform users with RBAC roles and token quota
- teacher_rules   : pedagogical rules injected by teachers into student prompts
- usage_stats     : daily token consumption aggregated per user
- sessions        : named chat sessions belonging to a user
- messages        : individual messages inside a session with token tracking
"""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from backend.app.database import Base

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UserRole(str, enum.Enum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class SenderType(str, enum.Enum):
    user = "user"
    llm = "llm"


# ---------------------------------------------------------------------------
# Helper: server-side UTC timestamp default
# ---------------------------------------------------------------------------

def _utcnow():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. Users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole"), nullable=False, default=UserRole.student
    )
    daily_token_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=50_000)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    usage_stats: Mapped[list["UsageStat"]] = relationship(
        "UsageStat", back_populates="user", cascade="all, delete-orphan"
    )
    # Rules *created by* this user (teacher)
    authored_rules: Mapped[list["TeacherRule"]] = relationship(
        "TeacherRule",
        foreign_keys="TeacherRule.teacher_id",
        back_populates="teacher",
        cascade="all, delete-orphan",
    )
    # Rules *targeting* this user (student)
    targeted_rules: Mapped[list["TeacherRule"]] = relationship(
        "TeacherRule",
        foreign_keys="TeacherRule.student_id",
        back_populates="student",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


# ---------------------------------------------------------------------------
# 2. TeacherRule
# ---------------------------------------------------------------------------


class TeacherRule(Base):
    __tablename__ = "teacher_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL student_id means the rule applies to ALL students of this teacher
    student_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rules_json: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    teacher: Mapped["User"] = relationship(
        "User", foreign_keys=[teacher_id], back_populates="authored_rules"
    )
    student: Mapped["User | None"] = relationship(
        "User", foreign_keys=[student_id], back_populates="targeted_rules"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TeacherRule id={self.id} teacher={self.teacher_id} "
            f"student={self.student_id} active={self.is_active}>"
        )


# ---------------------------------------------------------------------------
# 3. UsageStat
# ---------------------------------------------------------------------------


class UsageStat(Base):
    __tablename__ = "usage_stats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tokens_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="usage_stats")

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_usage_stat_user_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UsageStat user={self.user_id} date={self.date} "
            f"tokens={self.tokens_used}>"
        )


# ---------------------------------------------------------------------------
# 4. Session
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    applied_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teacher_rules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions")
    applied_rule: Mapped["TeacherRule | None"] = relationship("TeacherRule")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan", order_by="Message.created_at"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Session id={self.id} user={self.user_id} deleted={self.is_deleted}>"


# ---------------------------------------------------------------------------
# 5. Message
# ---------------------------------------------------------------------------


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender: Mapped[SenderType] = mapped_column(
        Enum(SenderType, name="sendertype"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Message id={self.id} session={self.session_id} "
            f"sender={self.sender} tokens={self.total_tokens}>"
        )
