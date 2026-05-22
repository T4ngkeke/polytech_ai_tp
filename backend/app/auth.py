"""
auth.py — Password hashing, JWT utilities, and FastAPI RBAC dependencies.

Security contract
-----------------
* Passwords are hashed with bcrypt (passlib).
* JWT payload contains ONLY ``sub`` (user_id as str) and ``exp``.
  Role, quota, and any other user fields are NEVER stored in the token.
* All RBAC decisions query the ``users`` table in real-time to prevent
  stale-token exploits (e.g. a deleted or demoted user whose token is
  still valid).

Public API
----------
hash_password(plain: str) -> str
verify_password(plain: str, hashed: str) -> bool
create_access_token(user_id: UUID) -> str
decode_access_token(token: str) -> UUID          # raises HTTP 401 on failure
get_current_user  (FastAPI Depends)              # any valid, non-deleted user
require_teacher   (FastAPI Depends)              # teacher OR admin
require_admin     (FastAPI Depends)              # admin only
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import get_db

# ---------------------------------------------------------------------------
# Internal helpers — NOT imported by other modules
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Use HTTPBearer instead of OAuth2PasswordBearer so Swagger accepts raw JWTs
_security = HTTPBearer()

# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the bcrypt *hashed* string."""
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT utilities
# ---------------------------------------------------------------------------


def create_access_token(user_id: uuid.UUID) -> str:
    """
    Create a signed JWT whose payload contains ONLY:
    - ``sub``: str(user_id)
    - ``exp``: expiry timestamp

    No role, quota, or other fields are included so that RBAC is always
    enforced from the live database state, not from a potentially stale token.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    """
    Decode and validate *token*.  Returns the ``user_id`` (UUID) embedded
    in the ``sub`` claim.

    Raises
    ------
    HTTPException 401  if the token is invalid, expired, or malformed.
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        sub: str | None = payload.get("sub")
        if sub is None:
            raise credentials_error
        return uuid.UUID(sub)
    except (JWTError, ValueError):
        raise credentials_error


# ---------------------------------------------------------------------------
# FastAPI dependency: get_current_user
# ---------------------------------------------------------------------------


async def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(_security),
    db: AsyncSession = Depends(get_db),
):
    """
    Decode the bearer token and return the matching **active** User row.

    Rejects with HTTP 401 if:
    - The token is invalid or expired.
    - No user with that ID exists in the database.
    - The user's ``is_deleted`` flag is True.

    The DB query is made on every request so that soft-deletes and role
    changes take effect immediately without waiting for tokens to expire.
    """
    # Lazy import to avoid circular imports at module load time
    from backend.app.models import User

    user_id = decode_access_token(auth.credentials)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account has been deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# FastAPI dependency: require_teacher
# ---------------------------------------------------------------------------


async def require_teacher(
    current_user=Depends(get_current_user),
):
    """
    Allow access only to users whose **live DB role** is ``teacher`` or
    ``admin``.  Raises HTTP 403 for any other role.
    """
    from backend.app.models import UserRole

    if current_user.role not in (UserRole.teacher, UserRole.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher or admin access required",
        )
    return current_user


# ---------------------------------------------------------------------------
# FastAPI dependency: require_admin
# ---------------------------------------------------------------------------


async def require_admin(
    current_user=Depends(get_current_user),
):
    """
    Allow access only to users whose **live DB role** is ``admin``.
    Raises HTTP 403 for every other role, including ``teacher``.
    """
    from backend.app.models import UserRole

    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
