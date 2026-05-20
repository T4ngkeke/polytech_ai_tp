"""
schemas.py — Pydantic v2 request / response schemas.

Mirrors the ORM models in models.py but decoupled from DB concerns.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import SenderType, UserRole

# ---------------------------------------------------------------------------
# Shared config — all response models accept ORM objects
# ---------------------------------------------------------------------------

_orm = ConfigDict(from_attributes=True)

# ===========================================================================
# Auth
# ===========================================================================


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ===========================================================================
# Users
# ===========================================================================


class UserBase(BaseModel):
    username: str
    role: UserRole


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    model_config = _orm

    id: uuid.UUID
    is_deleted: bool


class UserRoleUpdate(BaseModel):
    role: UserRole


# ===========================================================================
# Sessions
# ===========================================================================


class SessionResponse(BaseModel):
    model_config = _orm

    id: uuid.UUID
    user_id: uuid.UUID
    is_poison_active: bool
    poison_prompt: str | None
    is_deleted: bool
    created_at: datetime


class PoisonUpdate(BaseModel):
    """Payload for PUT /api/teacher/sessions/{session_id}/poison."""

    is_poison_active: bool
    poison_prompt: str | None = None


# ===========================================================================
# Messages
# ===========================================================================


class MessageResponse(BaseModel):
    model_config = _orm

    id: uuid.UUID
    session_id: uuid.UUID
    sender: SenderType
    content: str
    created_at: datetime


# ===========================================================================
# Chat
# ===========================================================================


class ChatRequest(BaseModel):
    session_id: uuid.UUID
    message: str
