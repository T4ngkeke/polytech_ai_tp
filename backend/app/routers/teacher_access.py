"""Helpers shared by the teacher route modules: storage root and class or lab access checks."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.models import Class, Lab, User, UserRole
from backend.app.services import class_service, lab_service

def get_storage_root() -> str:
    """Storage root for uploaded documents (overridable in tests)."""
    return settings.DOCUMENTS_STORAGE_ROOT


async def _verify_class_ownership(db: AsyncSession, class_id: uuid.UUID, teacher: User) -> Class:
    """Fetch a class (404 if missing) and assert the teacher owns it (403 otherwise)."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return cls


# The routes an admin uses too, on any class (the admin Classes page): listing
# labs and students, analytics, locking, renaming and deleting. The other
# teacher routes (rules, documents, exam mode...) stay the owner's alone.
async def _class_access(db: AsyncSession, class_id: uuid.UUID, user: User) -> Class:
    """A class for its teacher or an admin (404 if missing, 403 otherwise)."""
    if user.role == UserRole.admin:
        return await class_service.get_class_by_id(db, class_id)
    return await _verify_class_ownership(db, class_id, user)


async def _lab_access(db: AsyncSession, lab_id: uuid.UUID, user: User) -> Lab:
    """A lab for the teacher of its class or an admin (404 if missing, 403 otherwise)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    if user.role != UserRole.admin:
        await lab_service.verify_lab_ownership(db, lab, teacher_id=user.id)
    return lab
