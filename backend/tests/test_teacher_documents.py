"""
test_teacher_documents.py — teacher document upload endpoint.

POST /api/teacher/labs/{lab_id}/documents — ownership-checked, stores the PDF
and queues its parse job. Runs on SQLite via make_client; the storage root is an
overridable dependency pointing at a tmp dir. Only PDF is accepted; Markdown
and HTML went with the pre-MinerU pipeline (2026-10-15).

The form no longer asks for a type or an audience. Everything a teacher uploads
is course material and the whole class can see it, so the only remaining choice
is which lab it belongs to (or the whole class). The language is found from the
text when the PDF is indexed.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.main import app
from backend.app.models import (
    Class, DocChunk, Document, DocumentStatus, EMBEDDING_DIM, IngestionJob, Lab,
    UserRole,
)
from backend.app.routers.teacher import get_storage_root
from backend.app.config import settings
from backend.tests import pdf_fixtures
from backend.tests.conftest import make_client, make_user, add_current_revision

PDF = pdf_fixtures.course_pdf(1)




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
            files={"file": ("cm3.pdf", PDF, "application/pdf")},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["filename"] == "cm3.pdf"

    docs = (await db_session.execute(select(Document))).scalars().all()
    assert len(docs) == 1
    assert docs[0].lab_id == lab.id


@pytest.mark.asyncio
async def test_upload_needs_no_type_or_audience(db_session, tmp_path):
    """The two fields that used to be required are gone — an upload with nothing
    but the file succeeds."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("cm3.pdf", PDF, "application/pdf")},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_shared_upload_is_class_wide(db_session, tmp_path):
    """A document uploaded with shared=true is class-wide (lab_id NULL),
    reachable from every lab in that class."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("cours.pdf", PDF, "application/pdf")},
            data={"shared": "true"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert docs[0].lab_id is None        # class-wide shared
    assert docs[0].class_id == cls.id


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_format(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("notes.txt", b"plain text", "text/plain")},
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
            files={"file": ("cm3.pdf", PDF, "application/pdf")},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert docs == []


@pytest.mark.asyncio
async def test_a_delete_needs_the_generation_and_closes_the_document_at_once(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
                   storage_path="", content_hash=uuid.uuid4().hex,
                   status=DocumentStatus.indexed, uploaded_by=teacher.id)
    db_session.add(doc)
    await db_session.flush()
    await add_current_revision(db_session, doc, teacher.id)
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        without = await client.delete(f"/api/teacher/documents/{doc.id}")
        resp = await client.delete(f"/api/teacher/documents/{doc.id}?expected_generation={doc.generation}")
    finally:
        await client.aclose()

    assert without.status_code == 422 and without.json()["detail"] == "expected_generation_required"
    assert resp.status_code == 202 and resp.json()["state"] == "pending"
    await db_session.refresh(doc)
    assert doc.lifecycle == "deleted"      # the files go in the background (document_cleanup)


@pytest.mark.asyncio
async def test_delete_document_rejects_foreign_teacher(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex,
                   uploaded_by=teacher.id)
    db_session.add(doc)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()

    client = await make_client(db_session, intruder)
    try:
        resp = await client.delete(f"/api/teacher/documents/{doc.id}")
    finally:
        await client.aclose()

    assert resp.status_code == 404
    assert (await db_session.get(Document, doc.id)) is not None  # untouched


@pytest.mark.asyncio
@pytest.mark.parametrize("name,content,mime", [
    ("cours.md", b"# Recursivite\ncorps", "text/markdown"),
    ("cours.html", b"<h1>Recursivite</h1>", "text/html"),
])
async def test_markdown_and_html_are_refused(db_session, tmp_path, name, content, mime):
    """Only PDF goes to the course reader; other files are refused, not stored."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(f"/api/teacher/labs/{lab.id}/documents",
                                 files={"file": (name, content, mime)})
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 400 and resp.json()["detail"] == "only_pdf"
    assert (await db_session.execute(select(Document))).scalars().all() == []


@pytest.mark.asyncio
async def test_class_upload_is_class_wide(db_session, tmp_path):
    """An upload at the class level belongs to no lab: every lab reads it."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/classes/{cls.id}/documents",
            files={"file": ("poly.pdf", PDF, "application/pdf")},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["lab_id"] is None
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert [(d.class_id, d.lab_id) for d in docs] == [(cls.id, None)]


@pytest.mark.asyncio
async def test_teacher_cannot_upload_to_foreign_class(db_session, tmp_path):
    owner, cls, lab = await _teacher_with_lab(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, intruder)
    try:
        resp = await client.post(
            f"/api/teacher/classes/{cls.id}/documents",
            files={"file": ("poly.pdf", PDF, "application/pdf")},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert (await db_session.execute(select(Document))).scalars().all() == []
