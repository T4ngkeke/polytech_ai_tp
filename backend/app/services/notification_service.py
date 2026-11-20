"""notification_service.py — the notification bell.

A notification is one row for one user (models.Notification). The code that
makes an event happen adds the rows in its own transaction, so a notification
exists exactly when the event was saved. The page writes the text from
``kind`` and ``payload``, in the user's language.

Kinds and who gets them:

- ``exam_mode`` (students of the class): the teacher turned exam mode on or off.
- ``lab_locked`` (students of the class): a lab was locked or opened.
- ``new_document`` (students of the class): a course file became readable, or
  a new version of it was published (``updated``).
- ``document_ready`` / ``document_failed`` (the class teacher): a file the
  teacher uploaded was read; ``partial`` when some pages could not be read.
- ``quota_request`` (the student's teachers): a student at the limit asks for a reset.
- ``quota_reset`` / ``quota_ignored`` (the student): a teacher answered.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update

from ..models import Class, ClassStudent, Document, Lab, Notification, User

KINDS = ('exam_mode', 'lab_locked', 'new_document', 'document_ready', 'document_failed',
         'quota_request', 'quota_reset', 'quota_ignored')
LIST_LIMIT = 50


def _plain(value):
    """JSON-safe payload values (ids as text)."""
    return str(value) if isinstance(value, uuid.UUID) else value


def notify(db, user_ids, kind: str, **payload) -> int:
    """Add one notification per user (no commit: the caller's transaction)."""
    if kind not in KINDS:
        raise ValueError(f'unknown notification kind {kind!r}')
    data = {key: _plain(value) for key, value in payload.items()}
    ids = list(dict.fromkeys(user_ids))
    for user_id in ids:
        db.add(Notification(user_id=user_id, kind=kind, payload=data))
    return len(ids)


async def class_students(db, class_id: uuid.UUID) -> list[uuid.UUID]:
    return list((await db.scalars(select(ClassStudent.student_id)
        .join(User, User.id == ClassStudent.student_id)
        .where(ClassStudent.class_id == class_id, User.is_deleted.is_(False)))).all())


async def notify_class_students(db, for_class: uuid.UUID, kind: str, **payload) -> int:
    """Notify every student of a class (``for_class``); payload may name the class too."""
    return notify(db, await class_students(db, for_class), kind, **payload)


async def student_teachers(db, student_id: uuid.UUID) -> list[uuid.UUID]:
    """The teachers of the classes the student is in."""
    return list((await db.scalars(select(Class.teacher_id).distinct()
        .join(ClassStudent, ClassStudent.class_id == Class.id)
        .where(ClassStudent.student_id == student_id, Class.is_deleted.is_(False)))).all())


async def listing(db, user_id: uuid.UUID, limit: int = LIST_LIMIT) -> dict:
    """The user's latest notifications, newest first, and how many are unread."""
    rows = (await db.scalars(select(Notification).where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc()).limit(limit))).all()
    unread = await db.scalar(select(func.count()).select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None)))
    return {'items': [{'id': n.id, 'kind': n.kind, 'payload': n.payload or {},
                       'created_at': n.created_at, 'read': n.read_at is not None} for n in rows],
            'unread': unread or 0}


async def mark_read(db, user_id: uuid.UUID, ids: list[uuid.UUID] | None = None) -> int:
    """Mark the given notifications (or all) of this user as read."""
    query = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if ids is not None:
        query = query.where(Notification.id.in_(ids))
    result = await db.execute(query.values(read_at=datetime.now(timezone.utc))
                              .execution_options(synchronize_session=False))
    return result.rowcount or 0


async def withdraw_unread(db, user_id: uuid.UUID, kind: str, since: datetime) -> None:
    """Remove this user's unread notifications of a kind since a time (an undone event)."""
    await db.execute(delete(Notification).where(
        Notification.user_id == user_id, Notification.kind == kind,
        Notification.read_at.is_(None), Notification.created_at >= since)
        .execution_options(synchronize_session=False))


async def _document_names(db, document_id: uuid.UUID):
    row = (await db.execute(select(Document.filename, Document.class_id, Document.lab_id,
                                   Document.exam_allowed, Class.teacher_id, Class.name, Class.exam_mode, Lab.name)
        .join(Class, Class.id == Document.class_id)
        .outerjoin(Lab, Lab.id == Document.lab_id)
        .where(Document.id == document_id))).first()
    return row


async def notify_document_teacher(db, document_id: uuid.UUID, kind: str, **extra) -> None:
    """Tell the class teacher what happened to an uploaded file (ready, failed)."""
    row = await _document_names(db, document_id)
    if row is None:
        return
    filename, class_id, lab_id, _, teacher_id, class_name, _, lab_name = row
    notify(db, [teacher_id], kind, document_id=document_id, filename=filename, class_id=class_id,
           class_name=class_name, lab_id=lab_id, lab_name=lab_name, **extra)


async def notify_document_students(db, document_id: uuid.UUID, *, updated: bool) -> None:
    """Tell the students of the class that a course file can be read (or has a
    new version). Not during an exam for a file the exam keeps closed."""
    row = await _document_names(db, document_id)
    if row is None:
        return
    filename, class_id, lab_id, exam_allowed, _, class_name, exam_mode, lab_name = row
    if exam_mode and not exam_allowed:
        return
    await notify_class_students(db, class_id, 'new_document', document_id=document_id, filename=filename,
                                class_id=class_id, class_name=class_name, lab_id=lab_id,
                                lab_name=lab_name, updated=updated)
