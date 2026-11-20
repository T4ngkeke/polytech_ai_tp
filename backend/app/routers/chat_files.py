"""routers/chat_files.py — files students upload in the chat.

POST   /api/student/files               → upload one file to a lab (multipart: file, lab_id).
GET    /api/student/files?lab_id=       → the user's own files, newest first.
DELETE /api/student/files/{id}          → hide an own file (the teacher still sees it in the audit).
GET    /api/files/{id}/content          → the file itself: its owner, the class teacher, an admin.

What is kept and how is in services/chat_file_service.py. Uploading needs the
lab's ``upload`` feature (services/feature_service.py); exam mode turns it off.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import ChatFile, Class, ClassStudent, Lab, User, UserRole
from backend.app.routers.teacher_access import get_storage_root
from backend.app.services import chat_file_service, feature_service

router = APIRouter(tags=["chat files"])


class ChatFileResponse(BaseModel):
    id: uuid.UUID
    lab_id: uuid.UUID | None
    session_id: uuid.UUID | None
    filename: str
    mime: str
    size: int
    kind: str
    has_text: bool
    text_chars: int
    created_at: datetime

    model_config = {"from_attributes": True}


async def _lab_for_upload(db: AsyncSession, lab_id: uuid.UUID, user: User) -> Lab:
    """An open lab of a class the user joined (or owns: the teacher's test drive)
    where uploads are allowed now."""
    row = (await db.execute(select(Lab, Class).join(Class, Class.id == Lab.class_id).where(
        Lab.id == lab_id, Lab.is_deleted.is_(False), Class.is_deleted.is_(False)))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lab_not_found")
    lab, cls = row
    member = cls.teacher_id == user.id or await db.scalar(select(ClassStudent.student_id).where(
        ClassStudent.class_id == cls.id, ClassStudent.student_id == user.id))
    if not member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not_member")
    if not lab.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "lab_locked")
    allowed = feature_service.effective(cls.features, lab.feature_overrides, exam_mode=cls.exam_mode)
    if not allowed["upload"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "feature_off")
    return lab


@router.post("/api/student/files", response_model=ChatFileResponse, status_code=201)
async def upload_chat_file(
    lab_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ChatFileResponse:
    """Upload an image, a PDF or a text file (10 MB at most) to use in this lab's
    chat. The text of a PDF or text file is read now. Errors: 413
    file_too_large, 415 unsupported_type, 422 empty_file or unreadable_pdf,
    403 feature_off when the lab does not allow uploads."""
    await _lab_for_upload(db, lab_id, current_user)
    data = await file.read(chat_file_service.MAX_BYTES + 1)
    try:
        row = await chat_file_service.save(db, root=get_storage_root(), user_id=current_user.id,
                                           lab_id=lab_id, filename=file.filename, data=data)
    except chat_file_service.ChatFileError as exc:
        code = {"file_too_large": 413, "unsupported_type": 415}.get(exc.code, 422)
        raise HTTPException(code, exc.code) from None
    await db.commit()
    return ChatFileResponse.model_validate(row)


@router.get("/api/student/files", response_model=list[ChatFileResponse])
async def list_chat_files(
    lab_id: uuid.UUID | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[ChatFileResponse]:
    """The user's own files (not the hidden ones), newest first; ``lab_id`` filters."""
    query = select(ChatFile).where(ChatFile.user_id == current_user.id, ChatFile.deleted_at.is_(None))
    if lab_id is not None:
        query = query.where(ChatFile.lab_id == lab_id)
    rows = (await db.scalars(query.order_by(ChatFile.created_at.desc()).limit(100))).all()
    return [ChatFileResponse.model_validate(r) for r in rows]


@router.delete("/api/student/files/{file_id}", status_code=204)
async def hide_chat_file(
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Hide an own file: it leaves the user's list and cannot be attached again.
    It is kept for the class teacher's audit, like a deleted conversation."""
    row = await db.get(ChatFile, file_id)
    if row is None or row.user_id != current_user.id or row.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")
    row.deleted_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/api/files/{file_id}/content")
async def chat_file_content(
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> FileResponse:
    """The file itself: for its owner (until hidden), the teacher of its class, or an admin."""
    row = await db.get(ChatFile, file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")
    allowed = current_user.role == UserRole.admin or (
        row.user_id == current_user.id and row.deleted_at is None)
    if not allowed and row.lab_id is not None:
        teacher_id = await db.scalar(select(Class.teacher_id).join(Lab, Lab.class_id == Class.id)
                                     .where(Lab.id == row.lab_id))
        allowed = teacher_id == current_user.id
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_found")
    path = chat_file_service.stored_path(get_storage_root(), row)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_missing")
    return FileResponse(path, media_type=row.mime, filename=row.filename, content_disposition_type="inline")
