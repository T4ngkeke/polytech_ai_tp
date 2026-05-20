"""
auth.py — JWT utilities and FastAPI Depends chain.

Implements the three dependency functions specified in the contract:
  • get_current_user  — any valid JWT whose user is not deleted
  • require_teacher   — role == teacher OR admin
  • require_admin     — role == admin ONLY

Business logic (token creation, password verification) is scaffolded but
NOT implemented — returns will be filled in during the implementation phase.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User, UserRole

# ---------------------------------------------------------------------------
# OAuth2 scheme — expects Bearer token in Authorization header
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ---------------------------------------------------------------------------
# Token helpers (stubs — logic to be implemented later)
# ---------------------------------------------------------------------------


def create_access_token(user_id: str) -> str:
    """Create a JWT containing ONLY user_id (no role — contract requirement)."""
    raise NotImplementedError


def decode_access_token(token: str) -> str:
    """Decode JWT and return user_id. Raise HTTPException on failure."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Dependency: get_current_user
# Accepts any valid JWT whose user exists in DB and is NOT soft-deleted.
# ---------------------------------------------------------------------------


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Validate JWT, load user from DB in real-time (stale-token prevention),
    reject with 401 if user is soft-deleted.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Dependency: require_teacher
# Passes for role == teacher OR admin.
# ---------------------------------------------------------------------------


async def require_teacher(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role not in (UserRole.teacher, UserRole.admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return current_user


# ---------------------------------------------------------------------------
# Dependency: require_admin
# Passes ONLY for role == admin.
# ---------------------------------------------------------------------------


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return current_user
