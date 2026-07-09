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
    JSON,
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
    failed = "failed"
    # [v7.1] PDF rejected by the character-yield gate — returned to the teacher,
    # never silently ingested. Not an error; a request to re-export a clean PDF.
    needs_review = "needs_review"


class DocType(str, enum.Enum):
    """[v7.1] Course-document type — the deterministic routing signal, set at upload."""
    CM = "CM"   # cours magistral (lecture: slides or book) → chunk path
    TD = "TD"   # travaux dirigés (problem set) → exercise path
    TP = "TP"   # travaux pratiques (lab: exercises + code) → exercise path
    # [v8.0] standalone answer key → Answers only (no chunks, no exercises).
    corrige = "corrigé"


class Audience(str, enum.Enum):
    """[v7.1] Who a document is for. Students never retrieve `teacher`-audience docs."""
    student = "student"
    teacher = "teacher"


class JobStatus(str, enum.Enum):
    """Lifecycle of an ingestion job in the DB-as-queue."""
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class CoachingLevel(str, enum.Enum):
    """How much scaffolding the tutor applies, from the effort window."""
    neutral = "neutral"
    low = "low"
    high = "high"


class HintStatus(str, enum.Enum):
    """[v8.0] Teacher-reviewed hint lifecycle. Only `approved` reaches students."""
    none = "none"
    generating = "generating"
    pending_review = "pending_review"
    approved = "approved"
    failed = "failed"


class HintSource(str, enum.Enum):
    """[v8.0] How the hint was grounded (orthogonal to hint_status). `blind` = solved
    with no answer key → red-flagged, review-mandatory."""
    worked = "worked"
    derived = "derived"
    blind = "blind"
    none = "none"


class AnswerForm(str, enum.Enum):
    """[v8.0] Reliability tier of an uploaded answer — decides the derivation path."""
    worked = "worked"                    # full worked solution
    final_only = "final_only"            # result only → anchored derivation
    proof_no_process = "proof_no_process"  # proof with no steps → blind solve


class JobType(str, enum.Enum):
    """[v8.0] Ingestion-queue job kind (worker dispatches on it)."""
    ingest = "ingest"
    hint_generate = "hint_generate"


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
    # [v8.0] Dialogue state: the exercise this session is currently discussing,
    # so follow-ups ("et la question 2 ?") can omit the number. Updated on every
    # agentic-search hit; ON DELETE SET NULL so re-ingestion (which deletes and
    # rebuilds exercises) self-clears a stale pointer instead of dangling.
    current_exercise_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("exercises.id", ondelete="SET NULL"), nullable=True
    )
    # [v8.0] Teacher test-drive session: the owning teacher chats against their
    # own lab to preview the tutor. Excluded from analytics / router-training /
    # learner-profile writes; may preview pending_review hint drafts.
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [v8.0] The previous turn's route — powers the clarify anti-loop: a second
    # consecutive unresolved turn falls through to rag instead of clarifying again.
    last_route: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
# 10. Document — [v7] uploaded course material with ingestion state machine
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"

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
    # [v7.1] Deterministic routing metadata, set at upload (the API enforces presence;
    # the column is nullable so historical rows / fixtures remain valid).
    doc_type: Mapped[DocType | None] = mapped_column(
        Enum(DocType, name="doctype"), nullable=True
    )
    audience: Mapped[Audience | None] = mapped_column(
        Enum(Audience, name="audience"), nullable=True
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="documentstatus"),
        nullable=False,
        default=DocumentStatus.pending,
    )
    # [v8.0] This doc carries answers (TD/TP+answers mixed, or a standalone corrigé).
    has_answers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [v8.0] For a standalone corrigé: which exercise document its answers pair to
    # (pins pairing to one doc in a multi-file lab). NULL → pair lab-wide by number.
    answers_for_document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # [v7.1] Populated on `failed` and carries the `needs_review` rejection reason.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [v7.3] Document language (fr/en) — selects the BM25 tsvector config.
    # Default fr: the upload form pre-fills it, teachers usually never touch it.
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="fr")
    # [v7.1] Parsed page count — Phase 6 summary. Nullable until processed.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [v7.3] Reconciliation report written at ingest: numbering anomaly, gaps,
    # in-lab number collisions, whether the LLM re-segmented, and counts. The
    # teacher audits a warning list instead of re-reading the document.
    ingest_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document id={self.id} status={self.status} file={self.filename!r}>"


# ---------------------------------------------------------------------------
# 11. IngestionJob — [v7] DB-as-queue row polled by the ingestion worker
# ---------------------------------------------------------------------------


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="jobstatus"), nullable=False, default=JobStatus.queued
    )
    # [v8.0] Job kind (worker dispatches on it) + free-form payload (e.g. a hint job's
    # exercise ids / urgent flag). Urgent jobs use priority=0 (claimed ORDER BY priority).
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
# 12. DocChunk — [v7] embedded text chunk for RAG retrieval (pgvector)
# ---------------------------------------------------------------------------


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized tenant scope — filter-without-JOIN security boundary.
    class_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    lab_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    # [v7.1] Denormalized from Document for query-time routing + the audience filter.
    doc_type: Mapped[DocType | None] = mapped_column(
        Enum(DocType, name="doctype"), nullable=True
    )
    audience: Mapped[Audience | None] = mapped_column(
        Enum(Audience, name="audience"), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # The ORIGINAL chunk text — what citations and the teacher chunk-inspector show.
    content: Mapped[str] = mapped_column(Text, nullable=False)
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


# ---------------------------------------------------------------------------
# 13. Exercise — [v7] LLM-extracted structured exercise (agentic search target)
# ---------------------------------------------------------------------------


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    lab_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    # [v7.2] Denormalized from the source Document: students never retrieve
    # teacher-audience exercises (enforced in the search WHERE clause).
    audience: Mapped[Audience] = mapped_column(
        Enum(Audience, name="audience"), nullable=False, default=Audience.student, index=True
    )
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    # [v7.2] Canonical integer derived from `number` (Roman/Arabic/3.1 → one int)
    # via normalize_exercise_number. Decouples matching from the unstable label.
    number_normalized: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    # [v7.3] hints stay NULL at ingest. [v8.0 §10] tiered L1/L2/L3 JSON array,
    # written by the generation workflow (and teacher edits); only surfaced to
    # students once hint_status == approved (retrieval_service gates it).
    hints: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # [v8.0] Teacher-reviewed hint lifecycle. `hint_status` = review pipeline position
    # (only `approved` is injected for students); `hint_source` = how it was grounded.
    hint_status: Mapped[HintStatus] = mapped_column(
        Enum(HintStatus, name="hintstatus"), nullable=False, default=HintStatus.none
    )
    hint_source: Mapped[HintSource] = mapped_column(
        Enum(HintSource, name="hintsource"), nullable=False, default=HintSource.none
    )
    # 🔴 v7.1 red line: there is NO solution column. The corrigé is not ingested or
    # stored anywhere — only student-safe statements are kept, so nothing can leak.
    # Reserved for future Adaptive Tutoring (per-concept scoring). Nullable for now.
    concept: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # [v7.3] Teacher-corrected rows survive idempotent re-ingestion.
    edited_by_teacher: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Exercise id={self.id} number={self.number!r} doc={self.document_id}>"


# ---------------------------------------------------------------------------
# 13b. Answer — [v8.0] uploaded answers, main-DB (grilling decision B, no vault)
# ---------------------------------------------------------------------------
# The single source of truth for pairing + hint (re)generation. Answers are NOT
# secret (controlled classroom + SQL-hardcoded retrieval), so they live in the
# main DB — but the STUDENT path never touches this table: chat / agent / prompt /
# retrieval modules must not import `Answer` (guarded by test_answers_isolation).


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    # Paired to its exercise at hint-generation time → nullable until then.
    exercise_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("exercises.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The document this answer was ingested from (corrigé or answers-bearing TD/TP).
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    number_normalized: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Classified at generation time (§9 step 0) → nullable at ingest.
    answer_form: Mapped[AnswerForm | None] = mapped_column(
        Enum(AnswerForm, name="answerform"), nullable=True
    )
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    # A verified derivation (final_only path) — stored so regeneration reuses it.
    derivation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Answer id={self.id} num={self.number_raw!r} doc={self.document_id}>"


# ---------------------------------------------------------------------------
# 14. LearnerProfile — [v7] prompt-literacy coaching (per student + lab)
# ---------------------------------------------------------------------------


class LearnerProfile(Base):
    __tablename__ = "learner_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lab_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("labs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Smoothed sliding-window effort/clarity score (0..1).
    effort_ema: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coaching_level: Mapped[CoachingLevel] = mapped_column(
        Enum(CoachingLevel, name="coachinglevel"), nullable=False, default=CoachingLevel.neutral
    )
    # Throttle marker for the explicit "how to ask" tip.
    last_tip_msg_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    teacher_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "lab_id", name="uq_learner_profile_user_lab"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LearnerProfile user={self.user_id} lab={self.lab_id} "
            f"ema={self.effort_ema:.2f} level={self.coaching_level}>"
        )


# ---------------------------------------------------------------------------
# 15. RouterQueryLog — [v7.1] low-confidence routing telemetry
# ---------------------------------------------------------------------------


class RouterQueryLog(Base):
    """[v8.0] Write-only log of EVERY LLM-router decision (redefined from the v7.1
    low-confidence-only kNN telemetry).

    Records route + number attribution for each message so a future distilled
    router has a labelled source. `degraded` marks timeout/failure fallbacks — not
    a real label, so it must be excludable when distilling. `is_test` marks teacher
    test-drive traffic (also excluded). Nothing in the live path reads it back.
    """
    __tablename__ = "router_query_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(String(16), nullable=False)
    exercise_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # explicit | context | sticky | none — the resolved number's origin.
    number_source: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lab_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RouterQueryLog route={self.route} "
            f"num={self.exercise_number} src={self.number_source}>"
        )


# ---------------------------------------------------------------------------
# 16. AgentTraceLog — [v8.0] per-message observability
# ---------------------------------------------------------------------------


class AgentTraceLog(Base):
    """Write-only, one row per chat message: which route, per-node latencies,
    which fallbacks fired, retrieval/threshold counts, and the self-eval verdict.

    This is the data source that decides the self-eval loop's fate (bad-verdict
    rate / retry-flip rate / false-grounding rate) and calibrates the rerank
    threshold. Nothing in the live path reads it. Soft references (nullable GUIDs)
    for message/lab so the async write never fights row-creation ordering.
    """
    __tablename__ = "agent_trace_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    lab_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    route: Mapped[str] = mapped_column(String(32), nullable=False)
    # Per-node milliseconds: router / retrieval / rerank / selfeval / first-token.
    node_latencies: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Which fallbacks fired: router / embedding / rerank / all_filtered.
    degraded_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recall_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rerank_filtered_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    all_filtered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    retry_flipped: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Optional regex injection-attempt marker (log-only, teacher-audit sort key).
    red_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentTraceLog msg={self.message_id} route={self.route}>"
