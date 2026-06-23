"""
test_teacher_documents.py — v7 teacher document upload endpoint.

POST /api/teacher/labs/{lab_id}/documents — ownership-checked, stores the file
and enqueues ingestion. Runs on SQLite via make_client; the storage root is an
overridable dependency pointing at a tmp dir.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.main import app
from backend.app.models import (
    Audience, Class, Document, DocumentStatus, DocType, Lab, UserRole,
)
from backend.app.routers.teacher import get_storage_root
from backend.tests.conftest import make_client, make_user


async def _teacher_with_lab(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.commit()
    return teacher, cls, lab


@pytest.mark.asyncio
async def test_teacher_uploads_document_to_own_lab(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("td3.pdf", b"exercise sheet", "application/pdf")},
            data={"doc_type": "TD", "audience": "student"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["filename"] == "td3.pdf"

    docs = (await db_session.execute(select(Document))).scalars().all()
    assert len(docs) == 1
    assert docs[0].lab_id == lab.id
    # Upload metadata is captured — the deterministic routing signal.
    assert docs[0].doc_type == DocType.TD
    assert docs[0].audience == Audience.student


@pytest.mark.asyncio
async def test_upload_requires_doc_type_and_audience(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("td3.pdf", b"exercise sheet", "application/pdf")},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("notes.txt", b"plain text", "text/plain")},
            data={"doc_type": "CM", "audience": "student"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 400
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert docs == []


@pytest.mark.asyncio
async def test_teacher_cannot_upload_to_foreign_lab(db_session, tmp_path):
    owner, cls, lab = await _teacher_with_lab(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()

    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, intruder)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("td3.pdf", b"exercise sheet", "application/pdf")},
            data={"doc_type": "TD", "audience": "student"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert docs == []
