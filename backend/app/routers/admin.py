"""
routers/admin.py — Admin-only endpoints for user and quota management.

All endpoints are protected by ``require_admin`` — only users whose live
DB role is ``admin`` can access them.

Endpoints
---------
GET    /api/admin/users              →  List all non-deleted users.
POST   /api/admin/users              →  Create a new user.
PUT    /api/admin/users/{user_id}/quota  →  Update a user's daily token quota.
DELETE /api/admin/users/{user_id}    →  Soft-delete a user (is_deleted = True).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import hash_password, require_admin
from backend.app.database import get_db
from backend.app.models import User
from backend.app.schemas import (
    UpdateQuotaRequest,
    UserCreateRequest,
    UserResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ===================================================================
# GET /api/admin/users
# ===================================================================


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Return all non-deleted users with their role and daily_token_quota."""
    result = await db.execute(
        select(User).where(User.is_deleted.is_(False))
    )
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


# ===================================================================
# POST /api/admin/users
# ===================================================================


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Create a new platform user.

    Returns 409 if the username already exists.
    """
    # Check for duplicate username
    existing = await db.execute(
        select(User).where(User.username == body.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken",
        )

    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role,
        daily_token_quota=body.daily_token_quota,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# PUT /api/admin/users/{user_id}/quota
# ===================================================================


@router.put("/users/{user_id}/quota", response_model=UserResponse)
async def update_quota(
    user_id: uuid.UUID,
    body: UpdateQuotaRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the daily token quota for a specific (non-deleted) user."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.daily_token_quota = body.daily_token_quota
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


# ===================================================================
# DELETE /api/admin/users/{user_id}
# ===================================================================


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Soft-delete a user: sets ``is_deleted = True`` without removing the row.

    Returns 404 if the user doesn't exist or is already deleted.
    """
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or already deleted",
        )

    user.is_deleted = True
    db.add(user)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
