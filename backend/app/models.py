"""
models.py — SQLAlchemy ORM models for Edu-LLM v7 Agentic Class-Lab Architecture.

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
import json
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Index,
    text,
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import CHAR, Float, TypeDecorator
from pgvector.sqlalchemy import Vector

from backend.app.database import Base

# Embedding dimension for DocChunk vectors. Placeholder — set to match the
# configured embedding model (see SystemConfigs.EMBEDDING_MODEL) before prod.
EMBEDDING_DIM = 1024


# ---------------------------------------------------------------------------
# Dialect-aware column types
# ---------------------------------------------------------------------------
# These render native PostgreSQL types in production and SQLite-compatible
# types under the test suite — automatically, per dialect — so no metadata
# patching is needed and ORM queries bind the correct type on either backend.


class GUID(TypeDecorator):
    """UUID column: native `uuid` on PostgreSQL, CHAR(36) on SQLite."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class EmbeddingVector(TypeDecorator):
    """Embedding column: native pgvector `Vector` on PostgreSQL, JSON text on SQLite."""
    impl = Text
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return list(value) if dialect.name == "postgresql" else json.dumps(list(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if dialect.name == "postgresql" else json.loads(value)

    class comparator_factory(TypeDecorator.Comparator):
        """Expose pgvector distance operators through the decorator."""
        def cosine_distance(self, other):
            return self.op("<=>", return_type=Float)(other)

        def l2_distance(self, other):
            return self.op("<->", return_type=Float)(other)


class TSVector(TypeDecorator):
    """[v7.1] BM25 full-text column: native `tsvector` on PostgreSQL, Text on SQLite.

    Populated at ingest time via a Postgres `to_tsvector(...)` expression; on SQLite
    it degrades to plain text so the model/table still builds under the unit suite.
    """
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import TSVECTOR
            return dialect.type_descriptor(TSVECTOR())
        return dialect.type_descriptor(Text())

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


class DocumentStatus(str, enum.Enum):
    """Ingestion state machine for an uploaded course document."""
    pending = "pending"
    processing = "processing"
    indexed = "indexed"
    partially_indexed = "partially_indexed"
    failed = "failed"
    # [v7.1] PDF rejected by the character-yield gate — returned to the teacher,
    # never silently ingested. Not an error; a request to re-export a clean PDF.
    needs_review = "needs_review"


class JobStatus(str, enum.Enum):
    """Lifecycle of an ingestion job in the DB-as-queue."""
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class JobType(str, enum.Enum):
    """Ingestion-queue job kind. Only one kind survives, but the column stays so
    the worker's dispatch point is still there if a second one is ever added."""
    ingest = "ingest"


class MessageFeedback(str, enum.Enum):
    """[v8.0] Student thumbs on an assistant message — free golden-set labels."""
    up = "up"
    down = "down"


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
        GUID(), primary_key=True, default=uuid.uuid4
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
        GUID(), primary_key=True, default=uuid.uuid4
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
        GUID(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invite_code: Mapped[str] = mapped_column(String(6), unique=True, nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Exam mode: every lab of the class answers in Direct mode only (no course search).
    exam_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
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
        GUID(), ForeignKey("classes.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
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
        GUID(), primary_key=True, default=uuid.uuid4
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
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
        GUID(), primary_key=True, default=uuid.uuid4
    )
    level: Mapped[RuleLevel] = mapped_column(
        Enum(RuleLevel, name="rulelevel"), nullable=False
    )
    # Polymorphic target — points to a Class ID, Lab ID, or User ID
    # No FK constraint because it references different tables based on `level`
    target_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True
    )
    rules_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("level", "target_id", name="uq_rule_level_target"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Rule id={self.id} level={self.level} target={self.target_id} active={self.is_active}>"


class SkillPreset(Base):
    """[v7.2] A teacher's private, reusable instructor-style (`skill.md`) preset.

    Applying one to a class snapshot-copies its `content` into that class's
    `level=class` rule — the prompt-injection path is unchanged (it still reads
    `rules`); this table is just a template library.
    """
    __tablename__ = "skill_presets"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_teacher_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SkillPreset id={self.id} name={self.name!r} owner={self.owner_teacher_id}>"


# ---------------------------------------------------------------------------
# 7. UsageStat
# ---------------------------------------------------------------------------


class UsageStat(Base):
    __tablename__ = "usage_stats"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
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


class QuotaGrant(Base):
    """Tokens a teacher adds to a student's quota for one day.

    A student who used the whole daily quota is not stuck until tomorrow: the
    teacher tops it up. Usage itself (UsageStat) is never rewritten, so the
    statistics stay true, and every grant says who gave it.
    """
    __tablename__ = "quota_grants"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint("tokens > 0", name="ck_quota_grant_tokens"),
        Index("ix_quota_grants_user_date", "user_id", "date"),
    )


# ---------------------------------------------------------------------------
# 8. Session — now scoped to a Lab
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lab_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("labs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Teacher test-drive session: the owning teacher chats against their own lab
    # to preview the tutor. Excluded from analytics and the citation report.
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
        GUID(), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
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
    # [v7.2] Weighted billed tokens (prompt·α + completion·β) stored at write time
    # so per-lab/class analytics reconcile with the quota's billed accounting
    # (recomputing later would be wrong once admin changes α/β).
    billed_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [v8.0] Student thumbs up/down on an assistant message (nullable = no feedback).
    feedback: Mapped[MessageFeedback | None] = mapped_column(
        Enum(MessageFeedback, name="messagefeedback"), nullable=True
    )
    # [M4-E] An answer that is a question back (ask_student): {question, options}.
    clarification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The answer mode of an assistant answer: direct, search or research (NULL: older answers).
    answer_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
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


# ---------------------------------------------------------------------------
# 10. Document — uploaded course material with ingestion state machine
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("lifecycle IN ('active','archived','deleted')", name="ck_document_lifecycle"),
        CheckConstraint("generation >= 0", name="ck_document_generation"),
        ForeignKeyConstraint(["id", "current_revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_document_current_revision", use_alter=True),
    )
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    structured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lab_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("labs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="documentstatus"),
        nullable=False,
        default=DocumentStatus.pending,
    )
    # [v7.1] Populated on `failed` and carries the `needs_review` rejection reason.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [v7.3] Document language (fr/en) — selects the BM25 tsvector config.
    # Default fr: the upload form pre-fills it, teachers usually never touch it.
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="fr")
    # [v7.1] Parsed page count — Phase 6 summary. Nullable until processed.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Summary of the ingest run (chunk count, estimated token spend, any VLM
    # repairs) — the teacher audits this instead of re-reading the document.
    ingest_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document id={self.id} status={self.status} file={self.filename!r}>"


# ---------------------------------------------------------------------------
# 11. IngestionJob — DB-as-queue row polled by the ingestion worker
# ---------------------------------------------------------------------------


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "source_version_id"],
            ["document_sources.document_id", "document_sources.id"], name="fk_job_source"),
        ForeignKeyConstraint(["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"], name="fk_job_revision"),
        UniqueConstraint("revision_id", name="uq_job_revision"),
        CheckConstraint("(task_kind IS NULL AND source_version_id IS NULL AND revision_id IS NULL) OR "
                        "(task_kind IN ('odp_parse','pdf_parse','pdf_index') AND source_version_id IS NOT NULL AND revision_id IS NOT NULL)",
                        name="ck_job_binding"),
    )
    # M2: a structured job is bound to one source file and one draft revision.
    # Legacy rows leave all of these NULL; the legacy claim only sees those rows.
    task_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    # Who holds the claim, the document generation it saw, and until when it may
    # still write. A result is only committed while all three still match.
    lock_token: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    claimed_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="jobstatus"), nullable=False, default=JobStatus.queued
    )
    # Job kind (the worker's dispatch point) + free-form payload. Urgent jobs use
    # priority=0 (claimed ORDER BY priority).
    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="jobtype"), nullable=False, default=JobType.ingest
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IngestionJob id={self.id} doc={self.document_id} status={self.status}>"


# ---------------------------------------------------------------------------
# 12. DocChunk — embedded text chunk for RAG retrieval (pgvector)
# ---------------------------------------------------------------------------


class DocChunk(Base):
    __tablename__ = "doc_chunks"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"], name="fk_chunk_revision"),
        ForeignKeyConstraint(["revision_id", "block_id"],
            ["document_blocks.revision_id", "document_blocks.block_id"], name="fk_chunk_block"),
        CheckConstraint("(revision_id IS NULL AND block_id IS NULL) OR (revision_id IS NOT NULL AND block_id IS NOT NULL AND content_start IS NOT NULL AND content_end IS NOT NULL AND part_index IS NOT NULL AND part_count IS NOT NULL AND content_start >= 0 AND content_end > content_start AND part_index >= 0 AND part_count > part_index)", name="ck_chunk_range"),
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    block_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    part_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized tenant scope — filter-without-JOIN security boundary.
    class_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    lab_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Search/citation text: native, visually supplemented, or teacher-corrected.
    # source_text/source_ref retain its original provenance on the layout path.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Fingerprint of the worker's original text, unchanged by teacher edits.
    # Null on legacy rows: edited legacy rows require review before rebuilding.
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # [v7.1] LLM-generated Contextual-Retrieval context, prepended before embedding.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [v7.1] Heading/section path the chunk came from.
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Embedded over the augmented text (context + content). bge-m3 = 1024 dim.
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(EMBEDDING_DIM), nullable=False)
    # [v7.1] BM25 full-text index, built over the augmented text at ingest time.
    tsv: Mapped[str | None] = mapped_column(TSVector(), nullable=True)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [v7.3] Teacher-corrected rows survive idempotent re-ingestion.
    edited_by_teacher: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DocChunk id={self.id} doc={self.document_id} idx={self.chunk_index}>"


class DocumentReview(Base):
    """Human decisions over stable PDF sources, independent of run reports."""
    __tablename__ = 'document_reviews'
    __table_args__ = (
        # Legacy PDF reviews: one per document source key, exactly as before.
        Index('uq_review_legacy_source', 'document_id', 'source_key', unique=True,
              postgresql_where=text('revision_id IS NULL'), sqlite_where=text('revision_id IS NULL')),
        # M2-C structured reviews: one per revision source key.
        Index('uq_review_revision_source', 'revision_id', 'source_key', unique=True,
              postgresql_where=text('revision_id IS NOT NULL'), sqlite_where=text('revision_id IS NOT NULL')),
        ForeignKeyConstraint(['document_id', 'revision_id'],
            ['document_revisions.document_id', 'document_revisions.id'], name='fk_review_revision'),
        ForeignKeyConstraint(['document_id', 'source_version_id'],
            ['document_sources.document_id', 'document_sources.id'], name='fk_review_source'),
        ForeignKeyConstraint(['revision_id', 'block_id'],
            ['document_blocks.revision_id', 'document_blocks.block_id'], name='fk_review_block'),
        CheckConstraint("(revision_id IS NULL AND source_version_id IS NULL AND block_id IS NULL AND kind IS NULL) OR "
                        "(revision_id IS NOT NULL AND source_version_id IS NOT NULL AND "
                        "kind IN ('teacher_edit_unmatched','vision_description'))", name='ck_review_binding'),
    )
    # M2-C: a structured review belongs to one source, one revision and (usually)
    # one block. All four stay NULL on legacy PDF reviews.
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    block_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox: Mapped[list] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending')
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    final_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


# M1: immutable source identities and revision-scoped content. No relationships
# back to Document: legacy joins remain unambiguous and publication is explicit.
class DocumentSource(Base):
    __tablename__ = "document_sources"
    __table_args__ = (
        UniqueConstraint("document_id", "id", name="uq_source_document"),
        UniqueConstraint("document_id", "sha256", name="uq_source_hash"),
        ForeignKeyConstraint(["document_id", "replaces_source_id"],
            ["document_sources.document_id", "document_sources.id"], name="fk_source_replaces"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("documents.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(16))
    original_filename: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    replaces_source_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "id", name="uq_revision_document"),
        UniqueConstraint("id", "source_version_id", name="uq_revision_source"),
        ForeignKeyConstraint(["document_id", "source_version_id"],
            ["document_sources.document_id", "document_sources.id"], name="fk_revision_source"),
        ForeignKeyConstraint(["document_id", "parent_revision_id"],
            ["document_revisions.document_id", "document_revisions.id"], name="fk_revision_parent"),
        ForeignKeyConstraint(["source_version_id", "preview_pdf_asset_id"],
            ["document_assets.source_version_id", "document_assets.id"], name="fk_revision_preview", use_alter=True),
        # M2 adds parsed: content is stored but not indexed, so it cannot publish.
        CheckConstraint("processing_state IN ('queued','processing','parsed','ready','partial','failed')", name="ck_revision_processing"),
        CheckConstraint("publication_state IN ('draft','published','superseded')", name="ck_revision_publication"),
        CheckConstraint("reason IN ('upload','reparse','teacher_edit')", name="ck_revision_reason"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("documents.id"), index=True)
    source_version_id: Mapped[uuid.UUID] = mapped_column(GUID())
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    preview_pdf_asset_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(String(16), default="upload")
    processing_state: Mapped[str] = mapped_column(String(16), default="queued")
    publication_state: Mapped[str] = mapped_column(String(16), default="draft")
    reader_version: Mapped[str] = mapped_column(String(64), default="pending")
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    index_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentAsset(Base):
    __tablename__ = "document_assets"
    __table_args__ = (
        UniqueConstraint("source_version_id", "id", name="uq_asset_source"),
        CheckConstraint("access_class IN ('teacher_original','student_visible')", name="ck_asset_access"),
        # M2 adds formula_source (the original formula object) and figure_preview
        # (an object rendered on its own page; not a crop of the slide).
        CheckConstraint("kind IN ('embedded_image','figure_crop','page_preview','pdf_preview','formula_source','figure_preview')", name="ck_asset_kind"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    source_version_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("document_sources.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(128))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    access_class: Mapped[str] = mapped_column(String(32))


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("revision_id", "order_index", name="uq_page_order"),
        UniqueConstraint("revision_id", "page_id", "source_version_id", name="uq_page_source"),
        ForeignKeyConstraint(["revision_id", "source_version_id"],
            ["document_revisions.id", "document_revisions.source_version_id"], name="fk_page_revision"),
        ForeignKeyConstraint(["source_version_id", "preview_asset_id"],
            ["document_assets.source_version_id", "document_assets.id"], name="fk_page_preview"),
        CheckConstraint("order_index >= 0 AND source_slide_no > 0 AND preview_page_no > 0", name="ck_page_order"),
        CheckConstraint("state IN ('usable','partial','unavailable')", name="ck_page_state"),
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    page_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    source_version_id: Mapped[uuid.UUID] = mapped_column(GUID())
    order_index: Mapped[int] = mapped_column(Integer)
    source_slide_no: Mapped[int] = mapped_column(Integer)
    # M2: NULL while the preview mapping of this page is not proven.
    preview_page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="usable")
    issues: Mapped[list] = mapped_column(JSON, default=list)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    preview_asset_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


class DocumentBlock(Base):
    __tablename__ = "document_blocks"
    __table_args__ = (
        UniqueConstraint("revision_id", "page_id", "order_index", name="uq_block_order"),
        UniqueConstraint("revision_id", "block_id", "source_version_id", name="uq_block_source"),
        ForeignKeyConstraint(["revision_id", "page_id", "source_version_id"],
            ["document_pages.revision_id", "document_pages.page_id", "document_pages.source_version_id"], name="fk_block_page"),
        CheckConstraint("kind IN ('heading','text','code','formula','table','figure','navigation')", name="ck_block_kind"),
        CheckConstraint("content_format IN ('plain','markdown','latex')", name="ck_block_format"),
        CheckConstraint("source_completeness IN ('complete','incomplete','unknown')", name="ck_block_completeness"),
        CheckConstraint("index_state IN ('searchable','needs_review','excluded')", name="ck_block_index"),
        CheckConstraint("description_origin IN ('none','model','teacher')", name="ck_block_origin"),
        CheckConstraint("order_index >= 0", name="ck_block_order"),
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    block_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    source_version_id: Mapped[uuid.UUID] = mapped_column(GUID())
    page_id: Mapped[uuid.UUID] = mapped_column(GUID())
    order_index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    content_format: Mapped[str] = mapped_column(String(16), default="plain")
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    section_path: Mapped[list] = mapped_column(JSON, default=list)
    content_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_origin: Mapped[str] = mapped_column(String(16), default="none")
    source_completeness: Mapped[str] = mapped_column(String(16), default="unknown")
    index_state: Mapped[str] = mapped_column(String(16), default="needs_review")
    issues: Mapped[list] = mapped_column(JSON, default=list)
    edited_by_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    # M2: structure the plain content cannot hold, e.g. a table with its repeated
    # and merged cells, or which object a preview has to show. Null for legacy.
    structure: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DocumentBlockAsset(Base):
    __tablename__ = "document_block_assets"
    __table_args__ = (
        ForeignKeyConstraint(["revision_id", "block_id", "source_version_id"],
            ["document_blocks.revision_id", "document_blocks.block_id", "document_blocks.source_version_id"], name="fk_block_asset_block"),
        ForeignKeyConstraint(["source_version_id", "asset_id"],
            ["document_assets.source_version_id", "document_assets.id"], name="fk_block_asset_asset"),
        CheckConstraint("role IN ('primary','preview')", name="ck_block_asset_role"),
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    block_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    source_version_id: Mapped[uuid.UUID] = mapped_column(GUID())
    role: Mapped[str] = mapped_column(String(16), default="primary")
    bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)


class DocumentBlockLink(Base):
    __tablename__ = "document_block_links"
    __table_args__ = (
        ForeignKeyConstraint(["revision_id", "from_block_id"], ["document_blocks.revision_id", "document_blocks.block_id"], name="fk_link_from"),
        ForeignKeyConstraint(["revision_id", "to_block_id"], ["document_blocks.revision_id", "document_blocks.block_id"], name="fk_link_to"),
        CheckConstraint("relation IN ('continues','explains') AND basis IN ('explicit_source','teacher')", name="ck_link_basis"),
        CheckConstraint("from_block_id <> to_block_id", name="ck_link_distinct"),
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    from_block_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    to_block_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    relation: Mapped[str] = mapped_column(String(16), primary_key=True)
    basis: Mapped[str] = mapped_column(String(24))
    source_ref: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentCleanupJob(Base):
    __tablename__ = "document_cleanup_jobs"
    __table_args__ = (CheckConstraint("state IN ('pending','failed','complete')", name="ck_cleanup_state"),)
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("documents.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remaining_storage_keys: Mapped[list] = mapped_column(JSON, default=list)

class DocumentStagedObject(Base):
    """M2: a file written to storage before the row that will own it exists.

    The row is committed first, then the file is written. When the owning
    source or asset row commits, this row is deleted in the same transaction.
    Otherwise a cleanup sweep removes the file once the lease has passed, so a
    crash, a lost claim or a deletion never leaves an unrecorded file behind.
    No FK on document_id: a new upload stages its file before the document row.
    """
    __tablename__ = "document_staged_objects"
    __table_args__ = (CheckConstraint("purpose IN ('upload_source','parse_asset')", name="ck_staged_purpose"),)
    storage_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# Register ORM immutability checks after all model names exist.
from backend.app.services import document_snapshot_guards as _snapshot_guards  # noqa: E402,F401


# ---------------------------------------------------------------------------
# M4-C: research runs, durable citations and per-call accounting.
# Research is opt-in; these tables do not alter the legacy chat write path.
# ---------------------------------------------------------------------------


class AgentRun(Base):
    __tablename__ = 'agent_runs'
    __table_args__ = (
        UniqueConstraint('user_id', 'client_request_id', name='uq_agent_run_user_request'),
        Index('uq_agent_run_active_session', 'session_id', unique=True,
              postgresql_where=text("status IN ('running','cancelling')"),
              sqlite_where=text("status IN ('running','cancelling')")),
        CheckConstraint("status IN ('running','cancelling','finished','failed','cancelled')",
                        name='ck_agent_run_status'),
        CheckConstraint("settlement_status IN ('open','settled')",
                        name='ck_agent_run_settlement'),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('sessions.id', ondelete='CASCADE'), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='running')
    snapshot_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey('messages.id', ondelete='SET NULL'))
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey('messages.id', ondelete='SET NULL'))
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    settlement_status: Mapped[str] = mapped_column(String(16), nullable=False, default='open')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageCitation(Base):
    __tablename__ = 'message_citations'
    __table_args__ = (
        UniqueConstraint('message_id', 'label', name='uq_message_citation_label'),
        CheckConstraint('(range_start IS NULL AND range_end IS NULL) OR '
                        '(range_start IS NOT NULL AND range_end IS NOT NULL AND '
                        'range_start >= 0 AND range_end > range_start)',
                        name='ck_message_citation_range'),
        CheckConstraint("availability IN ('available','unavailable')",
                        name='ck_message_citation_availability'),
        CheckConstraint('pdf_page_no > 0', name='ck_message_citation_page'),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('messages.id', ondelete='CASCADE'), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(24), nullable=False)
    # Logical IDs deliberately have no document FK: cleanup leaves a tombstone.
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    page_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    pdf_page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    block_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    range_start: Mapped[int | None] = mapped_column(Integer)
    range_end: Mapped[int | None] = mapped_column(Integer)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    availability: Mapped[str] = mapped_column(String(16), nullable=False, default='available')


class SessionReadState(Base):
    # Unused since 2026-10-12: the follow-up "selected source" was removed and
    # nothing reads or writes this table. It stays so the ORM matches the m4c
    # migration; dropping it needs a new migration.
    __tablename__ = 'session_read_state'
    session_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('sessions.id', ondelete='CASCADE'), primary_key=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    focused_sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_used_citation_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    open_question: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ModelCallUsage(Base):
    __tablename__ = 'model_call_usage'
    __table_args__ = (
        CheckConstraint("usage_source IN ('actual','estimated','none')", name='ck_model_call_usage_source'),
        CheckConstraint('billed_tokens >= 0 AND elapsed_seconds >= 0', name='ck_model_call_cost'),
    )
    call_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_source: Mapped[str] = mapped_column(String(16), nullable=False)
    token_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    token_beta: Mapped[float] = mapped_column(Float, nullable=False)
    billed_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class UsageReservation(Base):
    __tablename__ = 'usage_reservations'
    __table_args__ = (
        CheckConstraint("state IN ('active','unknown','settled')", name='ck_usage_reservation_state'),
        CheckConstraint('reserved_tokens >= 0', name='ck_usage_reservation_amount'),
    )
    call_id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    token_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    token_beta: Mapped[float] = mapped_column(Float, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    settled_tokens: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='active')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
