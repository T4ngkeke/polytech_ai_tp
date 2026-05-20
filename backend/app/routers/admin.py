"""
routers/admin.py — Admin-only endpoints.

Contract endpoints:
  GET    /api/admin/users                    — list all users (incl. soft-deleted)
  POST   /api/admin/users                    — create a new user
  PUT    /api/admin/users/{user_id}/role     — change a user's role
  DELETE /api/admin/users/{user_id}          — soft-delete (is_deleted = True)
  DELETE /api/admin/users/{user_id}/hard     — hard-delete + cascade
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserResponse, UserRoleUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserResponse]:
    """List all users including soft-deleted ones (with is_deleted flag)."""
    return [{"status": "not implemented", "endpoint": "GET /api/admin/users"}]


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Create a new user with hashed password."""
    return {"status": "not implemented", "endpoint": "POST /api/admin/users"}


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Change a user's role."""
    return {
        "status": "not implemented",
        "endpoint": f"PUT /api/admin/users/{user_id}/role",
    }


@router.delete("/users/{user_id}", status_code=204)
async def soft_delete_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Soft-delete: set is_deleted = True."""
    return None


@router.delete("/users/{user_id}/hard", status_code=204)
async def hard_delete_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Hard-delete: physically remove user + cascade sessions/messages."""
    return None
