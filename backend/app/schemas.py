"""
schemas.py — Pydantic v2 request/response schemas for Edu-LLM v5 Class-Lab Architecture.

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
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)


class LLMConfigResponse(BaseModel):
    base_url: str
    api_key: str
    model: str


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


class DailyUsage(BaseModel):
    date: date
    tokens: int
    requests: int


class AdminAnalyticsResponse(BaseModel):
    total_tokens: int
    total_requests: int
    daily_breakdown: list[DailyUsage] = []


# ---------------------------------------------------------------------------
# Teacher: Class Analytics
# ---------------------------------------------------------------------------


class StudentUsageSummary(BaseModel):
    user_id: uuid.UUID
    username: str
    tokens_used: int
    request_count: int


class ClassAnalyticsResponse(BaseModel):
    class_id: uuid.UUID
    total_tokens: int
    total_requests: int
    students: list[StudentUsageSummary] = []


# ---------------------------------------------------------------------------
# Student: Session Update
# ---------------------------------------------------------------------------


class SessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


# ---------------------------------------------------------------------------
# Teacher: Invite Code Reset
# ---------------------------------------------------------------------------


class InviteCodeResponse(BaseModel):
    invite_code: str
