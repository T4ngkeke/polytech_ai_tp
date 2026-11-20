"""
schemas.py — Pydantic v2 request/response schemas for Edu-LLM.

Each router domain has its own section.  Models marked *Request* are used for
incoming payloads; models marked *Response* are returned to the client.

All schemas use UUIDs for IDs and datetime with timezone info (UTC).
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .services.feature_service import Override, class_features, lab_overrides

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


class MessageFeedbackValue(str, Enum):
    up = "up"
    down = "down"


class MessageFeedbackRequest(BaseModel):
    """A student's thumbs on an assistant message."""
    feedback: MessageFeedbackValue


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
    """Self-service password change — old password is verified."""
    # Not needed while the account must change its reset password.
    old_password: str | None = None
    new_password: str = Field(..., min_length=6)




# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserBase(BaseModel):
    username: str
    role: UserRole
    daily_token_quota: int
    weekly_token_quota: int


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_deleted: bool
    tour_seen_at: datetime | None = None
    must_change_password: bool = False
    ui_language: str | None = None
    about_me: str | None = None


ABOUT_ME_MAX_CHARS = 500


class UserSettingsRequest(BaseModel):
    """The user's own settings; the fields left out keep their value.
    ``null`` clears one (the page language then follows the browser)."""
    model_config = ConfigDict(extra="forbid")

    ui_language: Literal['zh', 'fr', 'en'] | None = None
    about_me: str | None = Field(None, max_length=ABOUT_ME_MAX_CHARS)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.student
    daily_token_quota: int = Field(5_000_000, ge=0, le=2_000_000_000)
    weekly_token_quota: int = Field(25_000_000, ge=0, le=2_000_000_000)


class UpdateQuotaRequest(BaseModel):
    daily_token_quota: int = Field(..., ge=0, le=2_000_000_000)
    # Left out: the weekly quota stays as it is.
    weekly_token_quota: int | None = Field(None, ge=0, le=2_000_000_000)


class UpdateRoleRequest(BaseModel):
    role: UserRole


# ---------------------------------------------------------------------------
# SystemConfig / LLM Config
# ---------------------------------------------------------------------------


class LLMConfigRequest(BaseModel):
    # Main generation engine. Optional since 2026-11-19 (the page sends only
    # what changed), but never empty when sent.
    base_url: str | None = Field(None, min_length=1)
    api_key: str | None = Field(None, min_length=1)
    model: str | None = Field(None, min_length=1)

    # model-routing table. All optional: only provided (non-None) fields
    # are upserted, so a legacy 3-field PUT never wipes the routing config.
    # Empty string = "inherit the main LLM" (see model_routing.resolve).
    embedding_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    rerank_url: str | None = None
    rerank_api_key: str | None = None
    rerank_model: str | None = None
    # 'on' / 'off': the reranker switch; 'off' keeps rerank_url.
    rerank_enabled: str | None = None
    # Agent slot (the research agent's model).
    agent_base_url: str | None = None
    agent_api_key: str | None = None
    agent_model: str | None = None
    # estimated-token budget for chat history (OOM protection).
    # VLM slot — images the research agent views and figure descriptions.
    # Empty model = feature off (never falls back — vision is opt-in).
    vlm_base_url: str | None = None
    vlm_api_key: str | None = None
    vlm_model: str | None = None
    # MinerU server of the PDF worker (hot-switched; empty = deployment setting).
    mineru_server_url: str | None = None
    # Its key; empty = the main model key.
    mineru_api_key: str | None = None
    # Picture captions at ingest: 'on'/'off', vLLM priority, seconds per document.
    picture_captions: str | None = None
    picture_caption_priority: str | None = None
    picture_caption_budget_seconds: str | None = None
    # Research agent limits (whole numbers; empty = default, see ResearchLimits).
    agent_max_tool_calls: str | None = None
    agent_max_visual_calls: str | None = None
    agent_research_seconds: str | None = None
    agent_total_seconds: str | None = None
    agent_call_seconds: str | None = None
    agent_max_input_tokens: str | None = None
    tp_context_percent: str | None = None
    history_context_percent: str | None = None
    token_alpha: float | None = None
    token_beta: float | None = None


class ServerCheckRequest(BaseModel):
    # The values of one server on the admin page, saved or not.
    url: str = Field(..., max_length=500)
    model: str = Field('', max_length=200)
    api_key: str = Field('', max_length=500)


class LLMConfigResponse(BaseModel):
    base_url: str
    api_key: str
    model: str
    # raw stored values — empty means "inherits the main LLM".
    embedding_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    rerank_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""
    rerank_enabled: str = ""
    # Raw stored values — agent slot empty = inherits main LLM.
    agent_base_url: str = ""
    agent_api_key: str = ""
    agent_model: str = ""
    # estimated-token budget for chat history (OOM protection).
    # VLM slot — empty model = vision off.
    vlm_base_url: str = ""
    vlm_api_key: str = ""
    vlm_model: str = ""
    mineru_server_url: str = ""
    mineru_api_key: str = ""
    picture_captions: str = ""
    picture_caption_priority: str = ""
    picture_caption_budget_seconds: str = ""
    # Feature flags of the deployment and the worker's own MinerU server (read-only).
    features: dict[str, bool] = Field(default_factory=dict)
    mineru_default_url: str = ""
    # Research agent limits — empty means the default.
    agent_max_tool_calls: str = ""
    agent_max_visual_calls: str = ""
    agent_research_seconds: str = ""
    agent_total_seconds: str = ""
    agent_call_seconds: str = ""
    agent_max_input_tokens: str = ""
    tp_context_percent: str = ""
    history_context_percent: str = ""
    # Default and allowed range of each research limit field, for the form:
    # {"agent_max_tool_calls": {"default": 30, "min": 1, "max": 40}, …}.
    research_limits: dict[str, dict[str, int]] = Field(default_factory=dict)
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
    exam_mode: bool = False
    # What students may use (services/feature_service.py), every feature present.
    features: dict[str, bool] = Field(default_factory=lambda: class_features(None))

    @field_validator("features", mode="before")
    @classmethod
    def _all_features(cls, value):
        return class_features(value)


class AdminClassResponse(ClassResponse):
    """A class in the admin Classes list: its teacher and a few counts.

    ``teacher_ok`` is false when the owner was deleted or is no longer a
    teacher or admin: nobody can manage the class until it is transferred.
    """
    teacher_username: str | None = None
    teacher_ok: bool = True
    lab_count: int = 0
    student_count: int = 0
    questions_7d: int = 0
    last_active: datetime | None = None


class ClassUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class ExamModeRequest(BaseModel):
    exam_mode: bool


class ClassFeaturesRequest(BaseModel):
    """Switch some features of a class; the ones left out keep their value."""
    model_config = ConfigDict(extra="forbid")

    upload: bool | None = None
    web: bool | None = None
    code: bool | None = None
    deep: bool | None = None


class LabFeaturesRequest(BaseModel):
    """This lab's own choice for some features: 'on', 'off', or 'inherit' (follow the class)."""
    model_config = ConfigDict(extra="forbid")

    upload: Override | None = None
    web: Override | None = None
    code: Override | None = None
    deep: Override | None = None


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
    # For students, in a class in exam mode: files they may still search (0: Direct only).
    exam_files: int = 0
    # This lab's own choices ('on', 'off', 'inherit'), and what students may
    # use here now (class switches, these choices and exam mode together).
    feature_overrides: dict[str, str] = Field(default_factory=lambda: lab_overrides(None))
    features: dict[str, bool] = Field(default_factory=dict)

    @field_validator("feature_overrides", mode="before")
    @classmethod
    def _all_overrides(cls, value):
        return lab_overrides(value)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_id: uuid.UUID
    lab_id: uuid.UUID | None
    filename: str
    status: str
    # Filled for structured (PDF) uploads; legacy uploads leave them empty.
    structured: bool = False
    generation: int | None = None
    source_version_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    processing_state: str | None = None
    job_id: uuid.UUID | None = None


class DocumentSummaryResponse(BaseModel):
    """A document with its processing status + summary for the teacher list."""
    id: uuid.UUID
    filename: str
    status: str
    page_count: int | None
    error_message: str | None
    chunk_count: int
    ingest_report: dict | None = None
    lifecycle: str = "active"
    generation: int = 0
    structured: bool = False
    current_revision_id: uuid.UUID | None = None
    # M2-B: the newest revision of a structured document, which may be a draft.
    # processing_state "parsed" means: content stored, waiting for indexing.
    latest_revision: dict | None = None
    # "class": shared by every lab of the class (lab_id NULL); "lab": this lab only.
    scope: str = "lab"


class AdminDocumentResponse(BaseModel):
    """A document in the admin Documents tab: where it is and how its upload went.

    The status shown is worked out in the browser as in the teacher list:
    a structured document not yet published shows its newest revision's state.
    """
    id: uuid.UUID
    filename: str
    status: str
    structured: bool = False
    current_revision_id: uuid.UUID | None = None
    page_count: int | None = None
    error_message: str | None = None
    class_id: uuid.UUID
    class_name: str
    lab_id: uuid.UUID | None = None
    lab_name: str | None = None
    uploaded_by: str | None = None
    uploaded_at: datetime | None = None
    latest_revision: dict | None = None


class ChunkResponse(BaseModel):
    """One indexed chunk — the 'is it well processed?' inspector surface."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    page_no: int | None
    section: str | None
    content: str
    context: str | None


class ChunkUpdateRequest(BaseModel):
    """Teacher correction of a chunk's text (triggers re-embed + re-index)."""
    content: str = Field(..., min_length=1)


class TestSessionResponse(BaseModel):
    """The id of a freshly created teacher test-drive session."""
    session_id: uuid.UUID


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


class ExamFilesRequest(BaseModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class RuleItemCreate(BaseModel):
    level: Literal["class", "lab", "student"]
    target_id: uuid.UUID
    # Required for a student rule: the class it applies in.
    class_id: uuid.UUID | None = None
    text: str = Field(..., min_length=1, max_length=1000)
    is_active: bool = True
    # 'b:<key>' a ready-made rule, 'm:<saved rule id>' one of the teacher's saved rules.
    source: str | None = Field(None, max_length=64)
    # Put the rule at this place in the list (an undone delete); default: last.
    position: int | None = Field(None, ge=0)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty rule")
        return value


class RuleItemUpdate(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=1000)
    is_active: bool | None = None
    # Only changed when sent; null unlinks the rule from its button.
    source: str | None = Field(None, max_length=64)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("empty rule")
        return value


class RuleItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level: str
    target_id: uuid.UUID
    class_id: uuid.UUID
    text: str
    source: str | None = None
    is_active: bool
    position: int


# ---------------------------------------------------------------------------
# Skill presets (instructor-style library)
# ---------------------------------------------------------------------------


class SkillPresetUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)


class SkillPresetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    content: str


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


class ReportRequest(BaseModel):
    """A teacher report: on the lab, or on one student with student_id."""
    student_id: uuid.UUID | None = None


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
    pinned: bool = False


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
    citations: list[dict] = []
    # A question back to the student: {"question": ..., "options": [...]}.
    clarification: dict | None = None
    # direct, search or research; None for answers written before the modes.
    answer_mode: str | None = None
    # A student question's attachments: [{kind, id, name}].
    attachments: list[dict] | None = None
    # Versions: the first question of the group, and when this one was replaced.
    version_of: uuid.UUID | None = None
    superseded_at: datetime | None = None


# ---------------------------------------------------------------------------
# Chat stream
# ---------------------------------------------------------------------------


# The longest question a student may send (a code file of a few hundred lines
# fits): about 7k tokens, next to a history of 30% of the context
# (agent/history.py). The chat page counts the characters and stops at it.
MAX_MESSAGE_CHARS = 20000


class ChatStreamRequest(BaseModel):
    session_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)
    client_request_id: uuid.UUID | None = None
    # direct: no course material; search: one search; research: the agent.
    # A class in exam mode answers in direct mode whatever is asked.
    mode: Literal['direct', 'search', 'research'] = 'research'
    # Tools the student turned on for this question; each must be allowed in
    # this lab (services/feature_service.py), else the question is refused.
    tools: list[Literal['web', 'code', 'deep']] = Field(default_factory=list, max_length=3)
    # What the student attaches to this question (checked by the chat):
    # an own uploaded file, a course file of this lab, or an own earlier chat.
    attachments: list["AttachmentRef"] = Field(default_factory=list, max_length=5)
    # A new version of the session's latest question (edit, or regenerate with
    # the same text): its id. The old question and answer stay for the audit.
    version_of: uuid.UUID | None = None


class AttachmentRef(BaseModel):
    kind: Literal['file', 'document', 'session']
    id: uuid.UUID


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
    # True: count what would be deleted, delete nothing (shown before confirming).
    dry_run: bool = False


class PruneResponse(BaseModel):
    # With dry_run, the rows that would be deleted.
    dry_run: bool = False
    sessions_deleted: int
    messages_deleted: int


class DailyUsage(BaseModel):
    date: date
    tokens: int
    requests: int


class AdminAnalyticsResponse(BaseModel):
    total_tokens: int
    total_requests: int
    active_users_7d: int = 0
    daily_breakdown: list[DailyUsage] = []


# ---------------------------------------------------------------------------
# Analytics — Hierarchical
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Student: Session Update & Usage
# ---------------------------------------------------------------------------


class SessionUpdateRequest(BaseModel):
    """Rename a session, pin or unpin it, or both."""
    title: str | None = Field(None, min_length=1, max_length=255)
    pinned: bool | None = None


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


class DocumentReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    page_no: int
    bbox: list[float]
    reason: str
    candidate_content: str | None
    status: Literal['pending','confirmed','ignored']
    revision: int
    final_content: str | None


class DocumentReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    status: Literal['confirmed','ignored']
    content: str | None = Field(default=None,max_length=20000)
