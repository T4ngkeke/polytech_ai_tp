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
    Answer, Audience, Class, DocChunk, Document, DocumentStatus, DocType,
    Exercise, IngestionJob, JobType, Lab, UserRole,
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
async def test_shared_cm_uploads_class_wide(db_session, tmp_path):
    """[v8.0] A CM uploaded with shared=true is class-wide (lab_id NULL), reachable
    from every lab of the class; the class tenant boundary is preserved."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("cm.pdf", b"lecture", "application/pdf")},
            data={"doc_type": "CM", "audience": "student", "shared": "true"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert len(docs) == 1
    assert docs[0].lab_id is None        # class-wide shared
    assert docs[0].class_id == cls.id    # tenant boundary kept


@pytest.mark.asyncio
async def test_corrige_upload_pins_target_document(db_session, tmp_path):
    """[v8.0 §10] A corrigé (answer file) may pin its target question document via
    answers_for_document_id, so pairing scopes to that TD (multi-file labs don't
    cross-match). The answer file only ever produces Answers — never exercises —
    so nothing leaks into student-visible statements."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    td = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
                  storage_path="/x", content_hash=uuid.uuid4().hex,
                  uploaded_by=teacher.id, doc_type=DocType.TD)
    db_session.add(td)
    await db_session.commit()

    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("corrige.pdf", b"answers", "application/pdf")},
            data={"doc_type": "corrigé", "audience": "student",
                  "answers_for_document_id": str(td.id)},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    corrige = (await db_session.execute(
        select(Document).where(Document.doc_type == DocType.corrige)
    )).scalar_one()
    assert corrige.answers_for_document_id == td.id


@pytest.mark.asyncio
async def test_shared_flag_ignored_for_td(db_session, tmp_path):
    """TD/TP stay strictly lab-scoped even if shared is passed."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("td.pdf", b"sheet", "application/pdf")},
            data={"doc_type": "TD", "audience": "student", "shared": "true"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert docs[0].lab_id == lab.id      # not shared — exercises are lab-level


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


@pytest.mark.asyncio
async def test_delete_document_removes_it_and_all_artifacts(db_session, tmp_path):
    """[v8.0] DELETE a document → the row, its chunks/exercises/jobs, the answers
    paired to its exercises, and the stored file are gone; a corrigé that pinned
    it has its pin nulled (the corrigé document survives). One endpoint serves
    CM / TD / TP / corrigé."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    stored = tmp_path / "cm.pdf"
    stored.write_bytes(b"lecture")
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
                   storage_path=str(stored), content_hash=uuid.uuid4().hex,
                   uploaded_by=teacher.id, doc_type=DocType.TD)
    corrige = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="c.pdf",
                       storage_path="/y", content_hash=uuid.uuid4().hex,
                       uploaded_by=teacher.id, doc_type=DocType.corrige)
    db_session.add_all([doc, corrige])
    await db_session.flush()
    corrige.answers_for_document_id = doc.id
    ex = Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                  number="1", number_normalized=1, statement="s")
    db_session.add(ex)
    await db_session.flush()
    db_session.add_all([
        DocChunk(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 doc_type=DocType.TD, audience=Audience.student, chunk_index=0,
                 content="c", context="ctx", embedding=[0.1, 0.2, 0.3]),
        Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="1",
               number_normalized=1, answer_text="42", exercise_id=ex.id),
        IngestionJob(id=uuid.uuid4(), document_id=doc.id, job_type=JobType.ingest),
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
        select(Exercise).where(Exercise.document_id == doc.id))).scalars().all() == []
    assert (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id))).scalars().all() == []
    assert (await db_session.execute(
        select(IngestionJob).where(IngestionJob.document_id == doc.id))).scalars().all() == []
    # The answer was paired to the deleted exercise — with the exercise gone it
    # has nothing to answer, so it goes too (even though it came from the corrigé).
    assert (await db_session.execute(select(Answer))).scalars().all() == []
    # The corrigé document itself survives, with its pin nulled.
    surviving = await db_session.get(Document, corrige.id)
    assert surviving is not None
    assert surviving.answers_for_document_id is None
    assert not stored.exists()  # stored file removed


@pytest.mark.asyncio
async def test_delete_document_rejects_foreign_teacher(db_session, tmp_path):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex,
                   uploaded_by=teacher.id, doc_type=DocType.CM)
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
async def test_upload_accepts_markdown(db_session, tmp_path):
    """[v8.1] .md is a first-class upload format (any doc_type), enqueued like a PDF."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("td3.md", b"# Exercice 1\ncorps", "text/markdown")},
            data={"doc_type": "TD", "audience": "student"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["filename"] == "td3.md"


@pytest.mark.asyncio
async def test_upload_accepts_html(db_session, tmp_path):
    """[v8.1] .html is a first-class upload format, enqueued like a PDF."""
    teacher, cls, lab = await _teacher_with_lab(db_session)
    app.dependency_overrides[get_storage_root] = lambda: str(tmp_path)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/labs/{lab.id}/documents",
            files={"file": ("td3.html", b"<h1>Exercice 1</h1><p>corps</p>", "text/html")},
            data={"doc_type": "TD", "audience": "student"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["filename"] == "td3.html"
