"""routers/notifications.py — the notification bell, for every role.

GET  /api/notifications       → the latest notifications and the unread count.
POST /api/notifications/read  → mark some (``ids``) or all (no ``ids``) as read.

Notifications are written where their event happens (services/notification_service.py).
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import User
from backend.app.services import notification_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class MarkReadRequest(BaseModel):
    ids: list[uuid.UUID] | None = None


@router.get("")
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """The user's latest notifications (newest first) and how many are unread.
    Each has ``kind`` and ``payload``; the page writes the text."""
    return await notification_service.listing(db, current_user.id)


@router.post("/read")
async def mark_notifications_read(
    body: MarkReadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Mark the given notifications as read, or all of them when ``ids`` is left out."""
    count = await notification_service.mark_read(db, current_user.id, body.ids)
    await db.commit()
    return {"marked": count}
