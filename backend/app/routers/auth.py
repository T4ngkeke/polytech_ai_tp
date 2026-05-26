"""
routers/auth.py — Authentication endpoints.

Endpoints
---------
POST /api/auth/login  →  Validate credentials, return JWT with user_id only.
GET  /api/auth/me     →  Return current user profile (id, username, role).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, get_current_user, verify_password
from backend.app.database import get_db
from backend.app.models import User
from backend.app.schemas import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect username or password",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Validate username + password, return a signed JWT.

    JWT payload contains ONLY ``sub`` (user_id) and ``exp`` —
    never role or quota — so that RBAC decisions always reflect
    the live database state.

    Returns HTTP 401 for:
    - Unknown username.
    - Wrong password.
    - Soft-deleted users (treat as non-existent).
    """
    result = await db.execute(
        select(User).where(User.username == body.username, User.is_deleted.is_(False))
    )
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise _INVALID_CREDENTIALS

    access_token = create_access_token(user_id=user.id)
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """
    Return the profile of the currently authenticated user.

    Used by the frontend to populate the auth store with ``userId``,
    ``username``, and ``role`` after login (since the JWT intentionally
    contains only ``sub``).
    """
    return UserResponse.model_validate(current_user)

