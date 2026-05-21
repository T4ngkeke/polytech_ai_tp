"""
routers/admin.py — Admin-only endpoints.

Contract endpoints:
  GET    /api/admin/users                — list ALL users (including soft-deleted)
  POST   /api/admin/users                — create a new user (hash password)
  PUT    /api/admin/users/{user_id}/role — change a user's role
  DELETE /api/admin/users/{user_id}      — soft-delete  (is_deleted = True)
  DELETE /api/admin/users/{user_id}/hard — hard-delete  + cascade sessions/messages
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, require_admin
from app.database import get_db
from app.models import Message, Session, User
from app.schemas import UserCreate, UserResponse, UserRoleUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_user_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[User]:
    """List ALL users, including soft-deleted ones (is_deleted flag visible)."""
    result = await db.execute(select(User).order_by(User.username))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# POST /api/admin/users
# ---------------------------------------------------------------------------


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Create a new user. Password is hashed with bcrypt before storage."""
    # Check for duplicate username.
    existing = await db.execute(
        select(User).where(User.username == payload.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken.",
        )

    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# PUT /api/admin/users/{user_id}/role
# ---------------------------------------------------------------------------


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Change a user's role. Works on soft-deleted users too (admin prerogative)."""
    user = await _get_user_or_404(db, user_id)
    user.role = payload.role
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# DELETE /api/admin/users/{user_id}  — soft delete
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}", status_code=204)
async def soft_delete_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Soft-delete: sets is_deleted = True. Sessions/messages are preserved."""
    user = await _get_user_or_404(db, user_id)
    user.is_deleted = True
    await db.commit()


# ---------------------------------------------------------------------------
# DELETE /api/admin/users/{user_id}/hard  — hard delete + cascade
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}/hard", status_code=204)
async def hard_delete_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """
    Hard-delete: physically removes the user and cascades to their sessions
    and messages via explicit bulk deletes (avoids async lazy-load issues).
    """
    # Confirm the user exists first.
    await _get_user_or_404(db, user_id)

    # 1. Collect session IDs for this user.
    session_ids_result = await db.execute(
        select(Session.id).where(Session.user_id == user_id)
    )
    session_ids = [row[0] for row in session_ids_result.all()]

    # 2. Delete all messages belonging to those sessions.
    if session_ids:
        await db.execute(delete(Message).where(Message.session_id.in_(session_ids)))

    # 3. Delete all sessions for this user.
    await db.execute(delete(Session).where(Session.user_id == user_id))

    # 4. Delete the user.
    await db.execute(delete(User).where(User.id == user_id))

    await db.commit()
