"""
document_service.py — [v7] course-document upload (producer side).

Stores uploaded bytes under a storage root, registers a `Document` in the
`pending` state, and enqueues an `IngestionJob` for the worker to pick up.
"""

import hashlib
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.exercise_number import normalize_heading_number
from backend.app.models import (
    Audience, Class, DocChunk, Document, DocType, Exercise, HintStatus,
    IngestionJob, JobType,
)

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


async def create_document(
    db: AsyncSession,
    *,
    class_id: uuid.UUID,
    lab_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID,
    storage_root: str | Path,
    doc_type: DocType | None = None,
    audience: Audience | None = None,
    language: str = "fr",
    answers_for_document_id: uuid.UUID | None = None,
) -> Document:
    """Persist an uploaded document and enqueue it for ingestion.

    [v8.0 §10] `answers_for_document_id` pins a corrigé (answer file) to its
    question document so pairing scopes to that TD in a multi-file lab."""
    content_hash = hashlib.sha256(content).hexdigest()

    # Dedup: identical content in the same scope is unchanged — return the
    # existing document without re-storing or re-enqueueing.
    existing = (
        await db.execute(
            select(Document).where(
                Document.class_id == class_id,
                Document.lab_id == lab_id,
                Document.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    root = Path(storage_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{content_hash}_{filename}"
    path.write_bytes(content)

    doc = Document(
        id=uuid.uuid4(),
        class_id=class_id,
        lab_id=lab_id,
        filename=filename,
        storage_path=str(path),
        content_hash=content_hash,
        doc_type=doc_type,
        audience=audience,
        language=language,
        answers_for_document_id=answers_for_document_id,
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()

    db.add(IngestionJob(id=uuid.uuid4(), document_id=doc.id))
    await db.commit()
    await db.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# [v7.1] Phase 6 — teacher ingestion visibility (read side)
# ---------------------------------------------------------------------------


async def _count(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return (await db.execute(stmt)).scalar_one()


async def list_lab_documents(db: AsyncSession, lab_id: uuid.UUID) -> list[dict]:
    """Documents in a lab with their status + processing summary (pages/chunks/exercises)."""
    docs = (await db.execute(
        select(Document).where(Document.lab_id == lab_id).order_by(Document.id)
    )).scalars().all()

    summaries: list[dict] = []
    for doc in docs:
        summaries.append({
            "doc": doc,
            "chunk_count": await _count(db, DocChunk, doc.id),
            "exercise_count": await _count(db, Exercise, doc.id),
        })
    return summaries


async def get_owned_document(
    db: AsyncSession, document_id: uuid.UUID, teacher_id: uuid.UUID
) -> Document | None:
    """Return the document only if it belongs to a class the teacher owns."""
    stmt = (
        select(Document)
        .join(Class, Class.id == Document.class_id)
        .where(Document.id == document_id, Class.teacher_id == teacher_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_document_chunks(
    db: AsyncSession, document_id: uuid.UUID, *, offset: int = 0, limit: int = 50
) -> list[DocChunk]:
    """Paginated chunks for a document, in source order — the inspector surface."""
    stmt = (
        select(DocChunk)
        .where(DocChunk.document_id == document_id)
        .order_by(DocChunk.chunk_index)
        .offset(offset)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_document_exercises(
    db: AsyncSession, document_id: uuid.UUID
) -> list[Exercise]:
    """Extracted exercises (statements only — no solution exists)."""
    stmt = (
        select(Exercise)
        .where(Exercise.document_id == document_id)
        .order_by(Exercise.number)
    )
    return list((await db.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# [v7.2] Teacher edits — correct chunking / extraction mistakes in place.
#
# The author is the ground-truth oracle for "was this processed well?", so they
# can fix a mis-split chunk or a wrong exercise. Editing a chunk re-embeds and
# re-indexes it so retrieval stays consistent with the corrected text.
# ---------------------------------------------------------------------------

def _augmented(context: str | None, content: str) -> str:
    """Text fed to the vector + BM25 indexes — must match worker/ingest._augmented."""
    return f"{context}\n{content}" if context else content


def _tsv_value(db: AsyncSession, text: str, language: str = "fr"):
    """BM25 tsvector (Postgres only; None elsewhere) — matches ingest._tsv_value.

    [v7.3] Config from the document's language: edit-side rebuilds must use the
    same config as ingest or the corrected chunk stops matching."""
    from backend.app.services.retrieval_service import ts_config_for

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_tsvector(ts_config_for(language), text)
    return None


async def get_chunk_in_document(
    db: AsyncSession, document_id: uuid.UUID, chunk_id: uuid.UUID
) -> DocChunk | None:
    """Fetch a chunk only if it belongs to the given document (scopes the edit)."""
    stmt = select(DocChunk).where(
        DocChunk.id == chunk_id, DocChunk.document_id == document_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def update_chunk(
    db: AsyncSession, chunk: DocChunk, *, content: str, embed_fn: EmbedFn
) -> DocChunk:
    """Replace a chunk's text and rebuild its retrieval artifacts.

    The original `content` is what citations/the inspector show; the vector and
    BM25 index are built over the *augmented* text (existing context + new
    content), so the teacher's correction is what students actually retrieve.
    """
    augmented = _augmented(chunk.context, content)
    embeddings = await embed_fn([augmented])
    parent = await db.get(Document, chunk.document_id)
    chunk.content = content
    chunk.embedding = embeddings[0]
    chunk.tsv = _tsv_value(db, augmented, parent.language if parent else "fr")
    # [v7.3] Flag the correction so idempotent re-ingestion preserves it.
    chunk.edited_by_teacher = True
    db.add(chunk)
    await db.flush()
    await db.refresh(chunk)
    return chunk


async def get_exercise_in_document(
    db: AsyncSession, document_id: uuid.UUID, exercise_id: uuid.UUID
) -> Exercise | None:
    """Fetch an exercise only if it belongs to the given document."""
    stmt = select(Exercise).where(
        Exercise.id == exercise_id, Exercise.document_id == document_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _make_hint_job(document: Document, exercise_ids, urgent: bool) -> IngestionJob:
    """A hint_generate job carrying its target exercise ids. Urgent jobs use
    priority=0 so the queue (ORDER BY priority) claims them ahead of ingestion."""
    return IngestionJob(
        id=uuid.uuid4(),
        document_id=document.id,
        job_type=JobType.hint_generate,
        payload={"exercise_ids": [str(i) for i in exercise_ids]},
        priority=0 if urgent else 100,
    )


async def start_batch_hint_generation(
    db: AsyncSession, document: Document, *, urgent: bool = False
) -> tuple[int, uuid.UUID | None]:
    """[v8.0 §10] Enqueue hint generation for every exercise in the document that
    still has no hints (hint_status == none). Idempotent: pending_review/approved/
    failed/edited exercises are skipped (single-exercise regen is their only entry).
    Marks the targeted exercises `generating`. Returns (count, job_id | None)."""
    exercises = (await db.execute(
        select(Exercise).where(
            Exercise.document_id == document.id,
            Exercise.hint_status == HintStatus.none,
        )
    )).scalars().all()
    if not exercises:
        return 0, None
    for exercise in exercises:
        exercise.hint_status = HintStatus.generating
    job = _make_hint_job(document, [e.id for e in exercises], urgent)
    db.add(job)
    await db.flush()
    return len(exercises), job.id


async def start_single_hint_generation(
    db: AsyncSession, document: Document, exercise: Exercise, *, urgent: bool = False
) -> uuid.UUID:
    """[v8.0 §10] Enqueue hint generation for one exercise regardless of its
    current status — the only entry that overwrites an already-reviewed/edited
    exercise. Marks it `generating`. Returns the job id."""
    exercise.hint_status = HintStatus.generating
    job = _make_hint_job(document, [exercise.id], urgent)
    db.add(job)
    await db.flush()
    return job.id


async def update_exercise(
    db: AsyncSession,
    exercise: Exercise,
    *,
    number: str | None = None,
    statement: str | None = None,
    hints: list[str] | None = None,
) -> Exercise:
    """Update an exercise's fields. Changing the label re-derives the canonical
    `number_normalized` via `normalize_heading_number` — the SAME normalizer
    ingest and answer pairing use, so a hand-edited label (e.g. a roman heading
    "III - Base 2 ...") stays paired with its corrigé answer."""
    if number is not None:
        exercise.number = number
        exercise.number_normalized = normalize_heading_number(number)
    if statement is not None:
        exercise.statement = statement
    if hints is not None:
        exercise.hints = hints
        # [v8.0 §10] Teacher-written hints are trusted — approved directly, no
        # self-review (they authored them).
        exercise.hint_status = HintStatus.approved
    # [v7.3] Flag the correction so idempotent re-ingestion preserves it.
    exercise.edited_by_teacher = True
    db.add(exercise)
    await db.flush()
    await db.refresh(exercise)
    return exercise


async def approve_exercise_hints(db: AsyncSession, exercise: Exercise) -> Exercise:
    """[v8.0 §10] Approve a reviewed draft → students can now see the hints
    (the retrieval gate only surfaces `approved`)."""
    exercise.hint_status = HintStatus.approved
    await db.flush()
    await db.refresh(exercise)
    return exercise


async def add_exercise(
    db: AsyncSession, document: Document, *,
    number: str, statement: str, hints: list[str] | None = None,
) -> Exercise:
    """[v8.0 §10] Hand-add an exercise the extractor missed. Marked
    edited_by_teacher so idempotent re-ingestion preserves it; the number is
    normalized by the same function used at ingest + query time."""
    exercise = Exercise(
        id=uuid.uuid4(),
        document_id=document.id,
        class_id=document.class_id,
        lab_id=document.lab_id,
        audience=document.audience,
        number=number,
        number_normalized=normalize_heading_number(number),
        statement=statement,
        hints=hints,
        hint_status=HintStatus.approved if hints else HintStatus.none,
        edited_by_teacher=True,
    )
    db.add(exercise)
    await db.flush()
    await db.refresh(exercise)
    return exercise


async def delete_exercise(db: AsyncSession, exercise: Exercise) -> None:
    """[v8.0 §10] Remove a phantom exercise (e.g. a TOC line mis-extracted)."""
    await db.delete(exercise)
    await db.flush()
