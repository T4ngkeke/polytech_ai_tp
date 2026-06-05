"""
services/class_service.py — Core business logic for Class management.

All functions receive an AsyncSession and return ORM objects or raise
HTTPException. Routers call these functions after performing their own
authorization checks (ownership for teachers, none for admins).
"""

import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Class, ClassStudent, User, UserRole


# ---------------------------------------------------------------------------
# Invite code generation
# ---------------------------------------------------------------------------

_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No 0/O/1/I


async def generate_unique_invite_code(db: AsyncSession) -> str:
    """Generate a collision-free 6-character alphanumeric invite code."""
    for _ in range(10):
        code = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(6))
        existing = await db.execute(select(Class).where(Class.invite_code == code))
        if existing.scalar_one_or_none() is None:
            return code
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to generate unique invite code after 10 attempts",
    )


# ---------------------------------------------------------------------------
# Class CRUD
# ---------------------------------------------------------------------------


async def create_class(db: AsyncSession, teacher_id: uuid.UUID, name: str) -> Class:
    """Create a new class with a unique invite code."""
    code = await generate_unique_invite_code(db)
    new_class = Class(name=name, teacher_id=teacher_id, invite_code=code)
    db.add(new_class)
    await db.flush()
    await db.refresh(new_class)
    return new_class


async def get_class_by_id(db: AsyncSession, class_id: uuid.UUID) -> Class:
    """Fetch a non-deleted class by ID. Raises 404 if not found."""
    result = await db.execute(
        select(Class).where(Class.id == class_id, Class.is_deleted.is_(False))
    )
    cls = result.scalar_one_or_none()
    if cls is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    return cls


async def list_classes_for_teacher(db: AsyncSession, teacher_id: uuid.UUID) -> list[Class]:
    """List all non-deleted classes owned by a specific teacher."""
    result = await db.execute(
        select(Class)
        .where(Class.teacher_id == teacher_id, Class.is_deleted.is_(False))
        .order_by(Class.created_at.desc())
    )
    return list(result.scalars().all())


async def list_all_classes(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Class]:
    """List all non-deleted classes system-wide (admin use, no ownership filter)."""
    result = await db.execute(
        select(Class)
        .where(Class.is_deleted.is_(False))
        .order_by(Class.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def rename_class(db: AsyncSession, cls: Class, name: str) -> Class:
    """Rename a class. Caller must have already verified ownership."""
    cls.name = name
    db.add(cls)
    await db.flush()
    await db.refresh(cls)
    return cls


async def soft_delete_class(db: AsyncSession, cls: Class) -> None:
    """Soft-delete a class. Caller must have already verified ownership."""
    cls.is_deleted = True
    db.add(cls)
    await db.flush()


async def reset_invite_code(db: AsyncSession, cls: Class) -> str:
    """Generate and save a new invite code. Returns the new code."""
    code = await generate_unique_invite_code(db)
    cls.invite_code = code
    db.add(cls)
    await db.flush()
    return code


async def transfer_class(db: AsyncSession, cls: Class, new_teacher_id: uuid.UUID) -> Class:
    """Force-change the owner of a class to a new teacher (admin only)."""
    # Verify new teacher exists and has the right role
    teacher_result = await db.execute(
        select(User).where(
            User.id == new_teacher_id,
            User.is_deleted.is_(False),
            User.role.in_([UserRole.teacher, UserRole.admin]),
        )
    )
    if teacher_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found or does not have teacher/admin role",
        )
    cls.teacher_id = new_teacher_id
    db.add(cls)
    await db.flush()
    await db.refresh(cls)
    return cls


# ---------------------------------------------------------------------------
# Student membership
# ---------------------------------------------------------------------------


async def list_students_in_class(db: AsyncSession, class_id: uuid.UUID) -> list[User]:
    """List all non-deleted students enrolled in a class."""
    result = await db.execute(
        select(User)
        .join(ClassStudent, ClassStudent.student_id == User.id)
        .where(ClassStudent.class_id == class_id, User.is_deleted.is_(False))
    )
    return list(result.scalars().all())


async def kick_student(
    db: AsyncSession, class_id: uuid.UUID, student_id: uuid.UUID
) -> None:
    """Remove a student from a class. Raises 404 if membership not found."""
    mem_result = await db.execute(
        select(ClassStudent).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_id == student_id,
        )
    )
    membership = mem_result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student is not enrolled in this class",
        )
    await db.delete(membership)
    await db.flush()
