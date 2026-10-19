"""
routers/monitoring.py — only admins of the app see Grafana.

Grafana runs without its own login, behind the frontend nginx under /grafana/.
The admin Monitoring tab first asks POST /session: the backend sets a cookie
valid for /grafana/ only (HttpOnly, 8 hours, signed for this use only). For
every /grafana/ request nginx asks GET /check (auth_request), which answers
204 when the cookie belongs to a user who is an admin now, and 401 otherwise.

POST   /api/admin/monitoring/session
GET    /api/admin/monitoring/check      (called by nginx, not by the app)
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_admin
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import User, UserRole

router = APIRouter(prefix="/api/admin/monitoring", tags=["monitoring"])

COOKIE = "edullm_monitoring"
AUDIENCE = "monitoring"
LIFETIME = timedelta(hours=8)


def monitoring_token(user_id: uuid.UUID, now: datetime | None = None) -> str:
    expire = (now or datetime.now(timezone.utc)) + LIFETIME
    return jwt.encode({"sub": str(user_id), "aud": AUDIENCE, "exp": expire},
                      settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def token_user(token: str | None) -> uuid.UUID | None:
    """The user of a monitoring token; None for anything else (an app token too)."""
    if not token:
        return None
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM],
                            audience=AUDIENCE)
        if claims.get("aud") != AUDIENCE:
            return None
        return uuid.UUID(claims["sub"])
    except (JWTError, KeyError, ValueError):
        return None


@router.post("/session", status_code=204)
async def open_session(request: Request, admin: User = Depends(require_admin)) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, monitoring_token(admin.id), max_age=int(LIFETIME.total_seconds()),
                        path="/grafana/", httponly=True, samesite="strict", secure=secure)
    return response


@router.get("/check")
async def check(request: Request, db: AsyncSession = Depends(get_db, scope="function")) -> Response:
    user_id = token_user(request.cookies.get(COOKIE))
    user = await db.get(User, user_id) if user_id else None
    allowed = user is not None and not user.is_deleted and user.role == UserRole.admin
    return Response(status_code=status.HTTP_204_NO_CONTENT if allowed else status.HTTP_401_UNAUTHORIZED)
