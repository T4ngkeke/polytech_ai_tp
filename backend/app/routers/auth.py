"""
routers/auth.py — Authentication endpoints.

Contract endpoint:
  POST /api/auth/login — validate credentials, return JWT containing ONLY user_id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, verify_password
from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """
    Validate username/password against the DB.
    Returns a JWT containing ONLY user_id — never the role.
    """
    # 1. Look up the user by username.
    result = await db.execute(select(User).where(User.username == payload.username))
    user: User | None = result.scalar_one_or_none()

    # 2. Verify existence and password. Use a generic error message to avoid
    #    leaking whether the username exists (timing-safe via bcrypt).
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Reject soft-deleted accounts at login time.
    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account has been deactivated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Issue JWT with user_id only.
    token = create_access_token(user.id)
    return TokenResponse(access_token=token)
