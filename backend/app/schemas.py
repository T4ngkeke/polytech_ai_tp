"""
schemas.py — Pydantic v2 request/response schemas for Edu-LLM v7 Agentic Class-Lab Architecture.

Each router domain has its own section.  Models marked *Request* are used for
incoming payloads; models marked *Response* are returned to the client.

All schemas use UUIDs for IDs and datetime with timezone info (UTC).
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared enums (mirrors SQLAlchemy enums)
# ---------------------------------------------------------------------------

from enum import Enum


class UserRole(str, Enum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class SenderType(str, Enum):
    user = "user"
    llm = "llm"


class RuleLevel(str, Enum):
    class_ = "class"
    lab = "lab"
    student = "student"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1)


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    """[v7.2] Self-service password change — old password is verified."""
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


class AdminResetPasswordRequest(BaseModel):
    """[v7.2] Admin reset of any user's password — no old password required."""
    new_password: str = Field(..., min_length=6)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserBase(BaseModel):
    username: str
    role: UserRole
    daily_token_quota: int


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_deleted: bool


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.student
    daily_token_quota: int = Field(50_000, ge=0)


class UpdateQuotaRequest(BaseModel):
    daily_token_quota: int = Field(..., ge=0)


class UpdateRoleRequest(BaseModel):
    role: UserRole


# ---------------------------------------------------------------------------
# SystemConfig / LLM Config
# ---------------------------------------------------------------------------


class LLMConfigRequest(BaseModel):
    # Main generation engine (required — always present).
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)

    # [v7.2] model-routing table. All optional: only provided (non-None) fields
    # are upserted, so a legacy 3-field PUT never wipes the routing config.
    # Empty string = "inherit the main LLM" (see model_routing.resolve).
    embedding_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    rerank_url: str | None = None
    rerank_api_key: str | None = None
    rerank_model: str | None = None
    ingest_base_url: str | None = None
    ingest_api_key: str | None = None
    ingest_model: str | None = None
    router_base_url: str | None = None
    router_api_key: str | None = None
    router_model: str | None = None
    rag_max_retries: str | None = None
    # [v7.3] hint slot (answer→tiered-hints big model) + injection gate + budgets.
    hint_base_url: str | None = None
    hint_api_key: str | None = None
    hint_model: str | None = None
    rerank_score_threshold: str | None = None
    hint_max_samples: str | None = None
    ingest_token_budget: str | None = None
    token_alpha: float | None = None
    token_beta: float | None = None


class LLMConfigResponse(BaseModel):
    base_url: str
    api_key: str
    model: str
    # [v7.2] raw stored values — empty means "inherits the main LLM".
    embedding_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    rerank_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""
    ingest_base_url: str = ""
    ingest_api_key: str = ""
    ingest_model: str = ""
    router_base_url: str = ""
    router_api_key: str = ""
    router_model: str = ""
    rag_max_retries: str = ""
    # [v7.3] raw stored values — hint slot empty = inherits main LLM;
    # threshold empty = injection gate OFF (until calibrated).
    hint_base_url: str = ""
    hint_api_key: str = ""
    hint_model: str = ""
    rerank_score_threshold: str = ""
    hint_max_samples: str = ""
    ingest_token_budget: str = ""
    token_alpha: float = 0.2
    token_beta: float = 1.0


# ---------------------------------------------------------------------------
# Class
# ---------------------------------------------------------------------------


class ClassCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class ClassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    teacher_id: uuid.UUID
    invite_code: str
    is_deleted: bool
    created_at: datetime


class ClassUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


# ---------------------------------------------------------------------------
# Lab
# ---------------------------------------------------------------------------


class LabCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class LabResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_id: uuid.UUID
    name: str
    is_deleted: bool
    is_active: bool
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_id: uuid.UUID
    lab_id: uuid.UUID | None
    filename: str
    status: str


class DocumentSummaryResponse(BaseModel):
    """[v7.1] A document with its processing status + summary for the teacher list."""
    id: uuid.UUID
    filename: str
    doc_type: str | None
    audience: str | None
    status: str
    page_count: int | None
    error_message: str | None
    chunk_count: int
    exercise_count: int


class ChunkResponse(BaseModel):
    """[v7.1] One indexed chunk — the 'is it well processed?' inspector surface."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    page_no: int | None
    section: str | None
    content: str
    context: str | None


class ChunkUpdateRequest(BaseModel):
    """[v7.2] Teacher correction of a chunk's text (triggers re-embed + re-index)."""
    content: str = Field(..., min_length=1)


class DocExerciseResponse(BaseModel):
    """[v7.1] An extracted exercise. Deliberately has no `solution`."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    statement: str
    hints: list[str] | None


class ExerciseUpdateRequest(BaseModel):
    """[v7.2] Teacher correction of an extracted exercise (statements only).
    [v8.0 §10] hints is a tiered L1/L2/L3 array; a teacher hand-edit is trusted."""
    number: str | None = Field(None, min_length=1, max_length=255)
    statement: str | None = Field(None, min_length=1)
    hints: list[str] | None = None


class LabUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# ClassStudent (Join)
# ---------------------------------------------------------------------------


class JoinClassRequest(BaseModel):
    invite_code: str = Field(..., min_length=6, max_length=6)


class JoinClassResponse(BaseModel):
    class_id: uuid.UUID
    class_name: str
    joined_at: datetime


# ---------------------------------------------------------------------------
# Rule (Polymorphic 3-tier)
# ---------------------------------------------------------------------------


class RuleUpsertRequest(BaseModel):
    level: RuleLevel
    target_id: uuid.UUID
    rules_text: str = Field(..., min_length=1)
    is_active: bool = True


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level: RuleLevel
    target_id: uuid.UUID
    rules_text: str
    is_active: bool


# ---------------------------------------------------------------------------
# [v7.2] Skill presets (instructor-style library)
# ---------------------------------------------------------------------------


class SkillPresetUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)


class SkillPresetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    content: str


class ApplySkillRequest(BaseModel):
    """Apply a preset (by id) or ad-hoc raw content to a class's skill rule."""
    preset_id: uuid.UUID | None = None
    content: str | None = None


# ---------------------------------------------------------------------------
# UsageStat
# ---------------------------------------------------------------------------


class UsageStatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    date: date
    tokens_used: int
    request_count: int


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    title: str | None = Field(None, max_length=255)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    lab_id: uuid.UUID | None = None
    title: str | None
    is_deleted: bool
    created_at: datetime


class SessionWithMessagesResponse(BaseModel):
    """Session with its full message history (used by teacher audit)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    lab_id: uuid.UUID | None = None
    title: str | None
    is_deleted: bool
    created_at: datetime
    messages: list["MessageResponse"] = []


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    sender: SenderType
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Chat stream
# ---------------------------------------------------------------------------


class ChatStreamRequest(BaseModel):
    session_id: uuid.UUID
    message: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------


class CSVImportPreviewResponse(BaseModel):
    """Response for admin CSV dry-run / import."""
    total: int = 0
    to_create: int = 0
    to_update: int = 0
    conflicts: list[dict] = []
    created: int = 0
    updated: int = 0
    forced: bool = False


# ---------------------------------------------------------------------------
# Teacher audit: student summary
# ---------------------------------------------------------------------------


class StudentSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    daily_token_quota: int
    tokens_used_today: int
    request_count_today: int


# ---------------------------------------------------------------------------
# Admin: Transfer, Analytics, Prune
# ---------------------------------------------------------------------------


class TransferClassRequest(BaseModel):
    teacher_id: uuid.UUID


class PruneRequest(BaseModel):
    older_than_days: int = Field(..., ge=1)


class PruneResponse(BaseModel):
    sessions_deleted: int
    messages_deleted: int
    router_logs_deleted: int = 0
    trace_logs_deleted: int = 0


class DailyUsage(BaseModel):
    date: date
    tokens: int
    requests: int


class AdminAnalyticsResponse(BaseModel):
    total_tokens: int
    total_requests: int
    daily_breakdown: list[DailyUsage] = []


# ---------------------------------------------------------------------------
# Analytics — Hierarchical (v7)
# ---------------------------------------------------------------------------


class StudentUsageSummary(BaseModel):
    user_id: uuid.UUID
    username: str
    tokens_used: int
    request_count: int

    @classmethod
    def from_analytics(cls, s) -> "StudentUsageSummary":
        """Build from an analytics_service.StudentUsage dataclass."""
        return cls(
            user_id=s.user_id,
            username=s.username,
            tokens_used=s.tokens_used,
            request_count=s.request_count,
        )


class LabUsageSummary(BaseModel):
    """Per-lab analytics with student-level breakdown."""
    lab_id: uuid.UUID
    lab_name: str
    tokens: int
    requests: int
    students: list[StudentUsageSummary] = []

    @classmethod
    def from_analytics(cls, lab) -> "LabUsageSummary":
        """Build from an analytics_service.LabUsage dataclass."""
        return cls(
            lab_id=lab.lab_id,
            lab_name=lab.lab_name,
            tokens=lab.tokens,
            requests=lab.requests,
            students=[StudentUsageSummary.from_analytics(s) for s in lab.students],
        )


class ClassAnalyticsResponse(BaseModel):
    """Hierarchical class analytics: class → lab → student."""
    class_id: uuid.UUID
    class_name: str
    total_tokens: int
    total_requests: int
    labs: list[LabUsageSummary] = []

    @classmethod
    def from_analytics(cls, result) -> "ClassAnalyticsResponse":
        """Build from an analytics_service.ClassAnalytics dataclass."""
        return cls(
            class_id=result.class_id,
            class_name=result.class_name,
            total_tokens=result.total_tokens,
            total_requests=result.total_requests,
            labs=[LabUsageSummary.from_analytics(lab) for lab in result.labs],
        )


# ---------------------------------------------------------------------------
# Student: Session Update & Usage
# ---------------------------------------------------------------------------


class SessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class StudentUsageResponse(BaseModel):
    """Today's token usage for the student."""
    used: int
    limit: int
    date: date


# ---------------------------------------------------------------------------
# Teacher: Invite Code Reset
# ---------------------------------------------------------------------------


class InviteCodeResponse(BaseModel):
    invite_code: str
