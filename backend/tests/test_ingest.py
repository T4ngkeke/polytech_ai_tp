"""
test_ingest.py — [v7.1] worker ingestion pipeline.

The pipeline takes injected parse / embed / extract / context functions, so it runs
on SQLite with fakes (no live LLM, embedding service, or real PDF needed). Covers:
  * structure-aware chunking + denormalized routing metadata,
  * statement-only exercise extraction on the TD/TP path (no solution),
  * Contextual Retrieval (augmented text embedded; original stored),
  * the character-yield gate → `needs_review` (never silently ingest garbage),
  * idempotent re-index and failure rollback.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Audience,
    Class,
    DocChunk,
    DocType,
    Document,
    DocumentStatus,
    Exercise,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.ingest import ingest_document
from backend.worker.parsing import GateResult


async def fake_embed(texts):
    return [[0.1, 0.2, 0.3] for _ in texts]


async def fake_extract(text):
    return [{"number": "Exercice 1", "statement": "Sum two numbers.", "hints": "use +"}]


async def _seed_document(
    session,
    tmp_path,
    *,
    body: str = "para one\n\npara two",
    doc_type: DocType = DocType.CM,
    audience: Audience = Audience.student,
):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    doc = await create_document(
        session, class_id=cls.id, lab_id=lab.id, filename="doc.txt",
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
        doc_type=doc_type, audience=audience,
    )
    return doc


@pytest.mark.asyncio
async def test_cm_ingest_writes_chunks_with_routing_metadata_no_exercises(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM)

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    assert len(chunks) == 2  # two paragraphs
    assert all(c.lab_id == doc.lab_id for c in chunks)
    # Routing metadata denormalized onto the chunk for query-time filtering.
    assert all(c.doc_type == DocType.CM for c in chunks)
    assert all(c.audience == Audience.student for c in chunks)

    # CM is not the exercise path — nothing extracted even though extract_fn was given.
    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert exercises == []


@pytest.mark.asyncio
async def test_td_ingest_extracts_statements_only(db_session, tmp_path):
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum two numbers.\n",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)

    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert len(exercises) == 1
    assert exercises[0].number == "Exercice 1"
    assert not hasattr(exercises[0], "solution")


@pytest.mark.asyncio
async def test_contextual_retrieval_embeds_augmented_stores_original(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM, body="lone slide fragment")

    embedded: list[str] = []

    async def capturing_embed(texts):
        embedded.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def fake_context(full_text, chunk_text):
        return "CONTEXT: about binary coding"

    await ingest_document(
        db_session, doc.id,
        embed_fn=capturing_embed, extract_fn=fake_extract, context_fn=fake_context,
    )

    chunk = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().one()
    # Original text stored for citation; generated context stored separately.
    assert chunk.content == "lone slide fragment"
    assert chunk.context == "CONTEXT: about binary coding"
    # The embedded text is the augmented one (context + original).
    assert any("CONTEXT: about binary coding" in t and "lone slide fragment" in t
               for t in embedded)


@pytest.mark.asyncio
async def test_bad_pdf_is_flagged_needs_review_not_ingested(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)

    def bad_parse(storage_path):
        return [], GateResult(ok=False, reason="Low character yield — looks scanned.")

    await ingest_document(
        db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract, parse_fn=bad_parse,
    )

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.needs_review
    assert "scanned" in (doc.error_message or "")
    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    assert chunks == []


@pytest.mark.asyncio
async def test_ingest_is_idempotent_on_rerun(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 1\nSum two numbers.\n")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)

    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert len(chunks) == 1
    assert len(exercises) == 1


async def boom_extract(text):
    raise ValueError("bad extract")


@pytest.mark.asyncio
async def test_ingest_marks_failed_and_rolls_back_on_error(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 1\nSum two numbers.\n")

    with pytest.raises(ValueError):
        await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=boom_extract)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert "bad extract" in (doc.error_message or "")
    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    assert chunks == []
