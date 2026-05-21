"""
auth.py — JWT utilities and FastAPI Depends chain.

Contract rules (from README §4):
  • JWT payload contains ONLY user_id — never the role.
  • get_current_user queries the DB in real-time to fetch the current role
    (stale-token prevention).
  • If the fetched user has is_deleted == True → 401 Unauthorized.
  • require_teacher  → role in {teacher, admin}   or 403 Forbidden.
  • require_admin    → role == admin ONLY           or 403 Forbidden.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User, UserRole

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# OAuth2 Bearer scheme (tokenUrl is used only by Swagger UI)
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def create_access_token(user_id: uuid.UUID) -> str:
    """
    Create a short-lived JWT whose payload contains ONLY user_id.
    No role is stored in the token (contract requirement).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    """
    Decode and validate a JWT. Returns the user_id string on success.
    Raises HTTP 401 on any failure (expired, malformed, bad signature).
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise _CREDENTIALS_EXCEPTION
        return user_id
    except JWTError:
        raise _CREDENTIALS_EXCEPTION


# ---------------------------------------------------------------------------
# Dependency: get_current_user
# ---------------------------------------------------------------------------


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    1. Decode the Bearer JWT → extract user_id.
    2. Query the Users table in real-time (stale-token prevention).
    3. Reject with 401 if user not found or is soft-deleted.
    """
    user_id_str = decode_access_token(token)

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        raise _CREDENTIALS_EXCEPTION

    result = await db.execute(select(User).where(User.id == user_uuid))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise _CREDENTIALS_EXCEPTION

    # Contract: soft-deleted users must be rejected immediately.
    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account has been deactivated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# ---------------------------------------------------------------------------
# Dependency: require_teacher  (teacher OR admin)
# ---------------------------------------------------------------------------


async def require_teacher(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role not in (UserRole.teacher, UserRole.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )
    return current_user


# ---------------------------------------------------------------------------
# Dependency: require_admin  (admin ONLY)
# ---------------------------------------------------------------------------


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role is not UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        )
    return current_user
