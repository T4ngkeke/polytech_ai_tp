"""
schemas.py — Pydantic v2 request/response schemas for Edu-LLM v3 Lean MVP.

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


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1)


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


# ---------------------------------------------------------------------------
# TeacherRule
# ---------------------------------------------------------------------------


class TeacherRuleBase(BaseModel):
    rules_json: Any
    is_active: bool = True


class TeacherRuleCreateRequest(TeacherRuleBase):
    student_id: uuid.UUID | None = None  # None → applies to all students


class TeacherRuleResponse(TeacherRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    teacher_id: uuid.UUID
    student_id: uuid.UUID | None


class TeacherRuleToggleResponse(BaseModel):
    id: uuid.UUID
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
    applied_rule_id: uuid.UUID | None = None
    title: str | None
    is_deleted: bool
    created_at: datetime


class SessionWithMessagesResponse(BaseModel):
    """Session with its full message history (used by teacher audit)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    applied_rule_id: uuid.UUID | None = None
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
# Teacher audit: student summary
# ---------------------------------------------------------------------------


class StudentSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    daily_token_quota: int
    tokens_used_today: int
    request_count_today: int
