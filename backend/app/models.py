"""
models.py — SQLAlchemy ORM models for Edu-LLM v5 Class-Lab Architecture.

Tables
------
- system_configs  : dynamic key-value config (LLM URL, API key, model)
- users           : platform users with RBAC roles and token quota
- classes         : teacher-owned classes with invite codes
- class_students  : association table linking students to classes
- labs            : labs scoped to a class
- rules           : polymorphic 3-tier rules (class / lab / student)
- sessions        : chat sessions scoped to a lab
- messages        : individual messages inside a session with token tracking
- usage_stats     : daily token consumption aggregated per user
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
from sqlalchemy.dialects.postgresql import UUID
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


class RuleLevel(str, enum.Enum):
    """Polymorphic rule target level."""
    class_ = "class"
    lab = "lab"
    student = "student"


# ---------------------------------------------------------------------------
# Helper: server-side UTC timestamp default
# ---------------------------------------------------------------------------

def _utcnow():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. SystemConfig — Dynamic key-value configuration
# ---------------------------------------------------------------------------


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SystemConfig key={self.key!r} value={self.value!r}>"


# ---------------------------------------------------------------------------
# 2. User
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
    # Classes this teacher owns
    classes_teaching: Mapped[list["Class"]] = relationship(
        "Class", back_populates="teacher", cascade="all, delete-orphan"
    )
    # Class memberships (student side)
    class_memberships: Mapped[list["ClassStudent"]] = relationship(
        "ClassStudent", back_populates="student", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


# ---------------------------------------------------------------------------
# 3. Class
# ---------------------------------------------------------------------------


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invite_code: Mapped[str] = mapped_column(String(6), unique=True, nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    teacher: Mapped["User"] = relationship("User", back_populates="classes_teaching")
    students: Mapped[list["ClassStudent"]] = relationship(
        "ClassStudent", back_populates="parent_class", cascade="all, delete-orphan"
    )
    labs: Mapped[list["Lab"]] = relationship(
        "Lab", back_populates="parent_class", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Class id={self.id} name={self.name!r} code={self.invite_code!r}>"


# ---------------------------------------------------------------------------
# 4. ClassStudent — Association table
# ---------------------------------------------------------------------------


class ClassStudent(Base):
    __tablename__ = "class_students"

    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    parent_class: Mapped["Class"] = relationship("Class", back_populates="students")
    student: Mapped["User"] = relationship("User", back_populates="class_memberships")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ClassStudent class={self.class_id} student={self.student_id}>"


# ---------------------------------------------------------------------------
# 5. Lab
# ---------------------------------------------------------------------------


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    parent_class: Mapped["Class"] = relationship("Class", back_populates="labs")
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="lab", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Lab id={self.id} name={self.name!r} class={self.class_id}>"


# ---------------------------------------------------------------------------
# 6. Rule — Polymorphic 3-tier (class / lab / student)
# ---------------------------------------------------------------------------


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    level: Mapped[RuleLevel] = mapped_column(
        Enum(RuleLevel, name="rulelevel"), nullable=False
    )
    # Polymorphic target — points to a Class ID, Lab ID, or User ID
    # No FK constraint because it references different tables based on `level`
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    rules_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("level", "target_id", name="uq_rule_level_target"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Rule id={self.id} level={self.level} target={self.target_id} active={self.is_active}>"


# ---------------------------------------------------------------------------
# 7. UsageStat
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
# 8. Session — now scoped to a Lab
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lab_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("labs.id", ondelete="CASCADE"),
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
    lab: Mapped["Lab | None"] = relationship("Lab", back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan", order_by="Message.created_at"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Session id={self.id} user={self.user_id} lab={self.lab_id} deleted={self.is_deleted}>"


# ---------------------------------------------------------------------------
# 9. Message
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
