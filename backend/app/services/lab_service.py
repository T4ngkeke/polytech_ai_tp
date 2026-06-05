"""
services/lab_service.py — Core business logic for Lab management.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Class, Lab


async def get_lab_by_id(db: AsyncSession, lab_id: uuid.UUID) -> Lab:
    """Fetch a non-deleted lab by ID. Raises 404 if not found."""
    result = await db.execute(
        select(Lab).where(Lab.id == lab_id, Lab.is_deleted.is_(False))
    )
    lab = result.scalar_one_or_none()
    if lab is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lab not found")
    return lab


async def verify_lab_ownership(db: AsyncSession, lab: Lab, teacher_id: uuid.UUID) -> None:
    """Verify that the lab's parent class is owned by teacher_id. Raises 403 if not."""
    cls_result = await db.execute(
        select(Class).where(Class.id == lab.class_id, Class.teacher_id == teacher_id)
    )
    if cls_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this lab",
        )


async def list_labs_for_class(db: AsyncSession, class_id: uuid.UUID) -> list[Lab]:
    """List all non-deleted labs for a class, ordered by creation date."""
    result = await db.execute(
        select(Lab)
        .where(Lab.class_id == class_id, Lab.is_deleted.is_(False))
        .order_by(Lab.created_at.desc())
    )
    return list(result.scalars().all())


async def list_all_labs(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Lab]:
    """List all non-deleted labs system-wide (admin use)."""
    result = await db.execute(
        select(Lab)
        .where(Lab.is_deleted.is_(False))
        .order_by(Lab.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def create_lab(db: AsyncSession, class_id: uuid.UUID, name: str) -> Lab:
    """Create a new lab in a class."""
    lab = Lab(class_id=class_id, name=name)
    db.add(lab)
    await db.flush()
    await db.refresh(lab)
    return lab


async def update_lab(
    db: AsyncSession,
    lab: Lab,
    name: str | None = None,
    is_active: bool | None = None,
) -> Lab:
    """Update lab name and/or is_active toggle."""
    if name is not None:
        lab.name = name
    if is_active is not None:
        lab.is_active = is_active
    db.add(lab)
    await db.flush()
    await db.refresh(lab)
    return lab


async def soft_delete_lab(db: AsyncSession, lab: Lab) -> None:
    """Soft-delete a lab."""
    lab.is_deleted = True
    db.add(lab)
    await db.flush()
