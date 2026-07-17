"""
test_documents.py — v7 ingestion data model tests.

Covers the Document state-machine table (pending → processing → indexed → failed).
"""

import uuid

import pytest

from backend.app.models import (
    Class,
    DocChunk,
    Document,
    DocumentStatus,
    Exercise,
    IngestionJob,
    JobStatus,
    Lab,
    UserRole,
)
from backend.tests.conftest import make_user


async def _make_teacher_and_class(db_session):
    """Persist a teacher + owned class, returning (teacher, class)."""
    teacher = make_user(role=UserRole.teacher)
    db_session.add(teacher)
    await db_session.flush()

    cls = Class(
        id=uuid.uuid4(),
        name="Algorithms",
        teacher_id=teacher.id,
        invite_code="ABC123",
    )
    db_session.add(cls)
    await db_session.flush()
    return teacher, cls


@pytest.mark.asyncio
async def test_document_defaults_to_pending_status(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        filename="td3.pdf",
        storage_path="/data/documents/td3.pdf",
        content_hash="deadbeef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)

    assert doc.status == DocumentStatus.pending


@pytest.mark.asyncio
async def test_document_lab_id_is_optional_for_class_wide_material(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        lab_id=None,
        filename="syllabus.pdf",
        storage_path="/data/documents/syllabus.pdf",
        content_hash="cafe",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)

    assert doc.lab_id is None


@pytest.mark.asyncio
async def test_ingestion_job_defaults_to_queued(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)
    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        filename="td3.pdf",
        storage_path="/data/documents/td3.pdf",
        content_hash="deadbeef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()

    job = IngestionJob(id=uuid.uuid4(), document_id=doc.id)
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)

    assert job.status == JobStatus.queued
    assert job.attempts == 0


@pytest.mark.asyncio
async def test_docchunk_stores_embedding_and_lab_scope(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Threads Lab")
    db_session.add(lab)
    await db_session.flush()

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        lab_id=lab.id,
        filename="threads.pdf",
        storage_path="/data/documents/threads.pdf",
        content_hash="beef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = DocChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        class_id=cls.id,
        lab_id=lab.id,
        chunk_index=0,
        content="Multithreading lets a process run multiple threads concurrently.",
        embedding=[0.1, 0.2, 0.3],
        page_no=1,
    )
    db_session.add(chunk)
    await db_session.flush()
    await db_session.refresh(chunk)

    assert chunk.lab_id == lab.id
    assert list(chunk.embedding) == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_exercise_concept_is_optional(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)
    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        filename="td3.pdf",
        storage_path="/data/documents/td3.pdf",
        content_hash="deadbeef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()

    ex = Exercise(
        id=uuid.uuid4(),
        document_id=doc.id,
        class_id=cls.id,
        number="Exercise 2",
        statement="Implement a thread-safe counter.",
        hints=["Think about locks."],
    )
    db_session.add(ex)
    await db_session.flush()
    await db_session.refresh(ex)

    assert ex.concept is None


# ---------------------------------------------------------------------------
# [v8.1] Markdown ingestion: _default_parse splits an .md file into heading
# pseudo-pages and still runs the character-yield gate (a renamed junk file
# must not slip through). Other non-PDF suffixes keep the single-page test
# fallback untouched.
# ---------------------------------------------------------------------------

def test_default_parse_md_splits_headings_and_gates(tmp_path):
    from backend.worker.ingest import _default_parse

    md = tmp_path / "td.md"
    md.write_text(
        "# Exercice 1\n" + ("Écrire une fonction qui additionne. " * 5)
        + "\n# Exercice 2\n" + ("Trier une liste avec le tri fusion. " * 5),
        encoding="utf-8",
    )
    pages, gate = _default_parse(str(md))
    assert len(pages) == 2
    assert pages[0].startswith("# Exercice 1")
    assert gate.ok


def test_default_parse_md_garbage_rejected_by_gate(tmp_path):
    from backend.worker.ingest import _default_parse

    junk = tmp_path / "junk.md"
    junk.write_text("��� " * 200, encoding="utf-8")  # no word chars
    pages, gate = _default_parse(str(junk))
    assert not gate.ok


def test_default_parse_other_suffix_keeps_single_page_fallback(tmp_path):
    from backend.worker.ingest import _default_parse

    txt = tmp_path / "notes.txt"
    txt.write_text("# looks like a heading\nbody", encoding="utf-8")
    pages, gate = _default_parse(str(txt))
    assert len(pages) == 1
    assert gate.ok


def test_default_parse_html_converts_and_pages(tmp_path):
    """[v8.1] .html goes through html_to_markdown then heading paging + gate."""
    from backend.worker.ingest import _default_parse

    page = tmp_path / "td.html"
    page.write_text(
        "<nav>menu</nav>"
        "<h1>Exercice 1</h1><p>" + ("Écrire une fonction. " * 10) + "</p>"
        "<h1>Exercice 2</h1><p>" + ("Trier une liste. " * 10) + "</p>",
        encoding="utf-8",
    )
    pages, gate = _default_parse(str(page))
    assert len(pages) == 2
    assert pages[0].startswith("# Exercice 1")
    assert all("menu" not in p for p in pages)
    assert gate.ok
