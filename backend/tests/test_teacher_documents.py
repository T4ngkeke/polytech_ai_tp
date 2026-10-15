"""
test_teacher_documents.py — teacher document upload endpoint.

POST /api/teacher/labs/{lab_id}/documents — ownership-checked, stores the PDF
and queues its parse job. Runs on SQLite via make_client; the storage root is an
overridable dependency pointing at a tmp dir. Only PDF is accepted; Markdown
and HTML went with the pre-MinerU pipeline (2026-10-15).

The form no longer asks for a type or an audience. Everything a teacher uploads
is course material and the whole class can see it, so the only remaining choices
are which lab it belongs to (or the whole class) and what language it is in.
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
from backend.tests.conftest import make_client, make_user

PDF = pdf_fixtures.course_pdf(1)


@pytest.fixture(autouse=True)
def structured_on(monkeypatch):
    monkeypatch.setattr(settings, "STRUCTURED_INGEST_ENABLED", True)
    monkeypatch.setattr(settings, "PDF_STRUCTURED_INGEST_ENABLED", True)


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
async def test_delete_document_removes_it_and_all_artifacts(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    stored = tmp_path / "doc.pdf"
    stored.write_bytes(b"slides")
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
                   storage_path=str(stored), content_hash=uuid.uuid4().hex,
                   status=DocumentStatus.indexed, uploaded_by=teacher.id)
    db_session.add(doc)
    await db_session.flush()
    db_session.add_all([
        DocChunk(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 chunk_index=0, content="a chunk", page_no=1,
                 embedding=[0.0] * EMBEDDING_DIM),
        IngestionJob(id=uuid.uuid4(), document_id=doc.id),
    ])
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.delete(f"/api/teacher/documents/{doc.id}")
    finally:
        await client.aclose()

    assert resp.status_code == 204
    assert (await db_session.get(Document, doc.id)) is None
    assert (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id))).scalars().all() == []
    assert not stored.exists()  # stored file removed


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
async def test_a_pdf_is_refused_while_structured_ingestion_is_off(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STRUCTURED_INGEST_ENABLED", False)
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(f"/api/teacher/labs/{lab.id}/documents",
                                 files={"file": ("cours.pdf", PDF, "application/pdf")})
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 409 and resp.json()["detail"] == "structured_ingest_disabled"


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
