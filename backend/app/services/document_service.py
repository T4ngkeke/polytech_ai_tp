"""
document_service.py — [v7] course-document upload (producer side).

Stores uploaded bytes under a storage root, registers a `Document` in the
`pending` state, and enqueues an `IngestionJob` for the worker to pick up.
"""

import hashlib
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.exercise_number import normalize_heading_number
from backend.app.models import (
    Answer, Audience, Class, DocChunk, Document, DocType, Exercise, HintStatus,
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


async def get_answer_in_document(
    db: AsyncSession, document_id: uuid.UUID, answer_id: uuid.UUID
) -> Answer | None:
    """An answer row, scoped to the document it was ingested from (ownership is
    checked on the document by the caller)."""
    stmt = select(Answer).where(
        Answer.id == answer_id, Answer.document_id == document_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def delete_answer(db: AsyncSession, answer: Answer) -> None:
    """[v8.0] Remove one uploaded answer (a mis-segmented or duplicate row)."""
    await db.delete(answer)
    await db.flush()


async def delete_document(db: AsyncSession, document: Document) -> str:
    """[v8.0] Delete a document and every artifact derived from it — chunks,
    exercises, the answers paired to those exercises (with the exercise gone the
    answer has nothing to answer), any answers ingested from this document, and
    its ingestion jobs — then the row itself. A corrigé that pinned this document
    as its answer target has its pin nulled (the corrigé document survives, minus
    the answer rows that were paired here). Children are removed explicitly (not
    left to DB cascade) so the behaviour is identical on SQLite and Postgres.

    Returns the `storage_path` so the caller can remove the stored file after the
    transaction commits (filesystem side effects stay in the router)."""
    storage_path = document.storage_path
    doc_id = document.id
    await db.execute(delete(DocChunk).where(DocChunk.document_id == doc_id))
    # Answers paired to this document's exercises (a corrigé's rows included) go
    # too: with the exercise gone, the answer has nothing to answer. Done
    # explicitly so SQLite matches the Answer.exercise_id CASCADE on Postgres.
    await db.execute(
        delete(Answer).where(Answer.exercise_id.in_(
            select(Exercise.id).where(Exercise.document_id == doc_id)
        ))
    )
    await db.execute(delete(Exercise).where(Exercise.document_id == doc_id))
    # Answers that were ingested FROM this document (a corrigé, or a has_answers
    # TD/TP) go with it.
    await db.execute(delete(Answer).where(Answer.document_id == doc_id))
    await db.execute(delete(IngestionJob).where(IngestionJob.document_id == doc_id))
    await db.execute(
        update(Document)
        .where(Document.answers_for_document_id == doc_id)
        .values(answers_for_document_id=None)
    )
    await db.delete(document)
    await db.flush()
    return storage_path


# ===================================================================
# [v8.1] Segmentation human-confirm (regex/LLM disagreement)
# ===================================================================


def get_segmentation(document: Document) -> dict:
    """Both candidate segmentations for the compare UI. `current` is the live
    split; `alternate` is the other candidate (None when the two agreed)."""
    cache = document.segmentation_cache or {}
    report = document.ingest_report or {}
    return {
        "chosen": cache.get("chosen"),
        "disagreement": bool(cache.get("disagreement", False)),
        "confirmed": bool(report.get("segmentation_confirmed", False)),
        "current": cache.get("segments") or [],
        "alternate": cache.get("alternate_segments"),
    }


async def choose_segmentation(
    db: AsyncSession, document: Document, which: str
) -> None:
    """Teacher confirms which candidate segmentation is right.

    Confirming the live one records the confirmation (the warning clears) and
    touches nothing else. Switching rebuilds the document's exercises
    deterministically from the stored alternate — no LLM, no re-ingest:
    teacher-edited exercises survive verbatim (unmatched ones re-added, never
    dropped), other hints are carried by number with `approved` demoted to
    `pending_review` (the statement changed → must be re-reviewed). The cache
    swaps so idempotent re-ingestion respects the choice.
    """
    cache = document.segmentation_cache or {}
    if not cache.get("segments"):
        raise ValueError("No stored segmentation to choose from.")
    if which == cache.get("chosen"):
        document.ingest_report = {
            **(document.ingest_report or {}), "segmentation_confirmed": True,
        }
        await db.flush()
        return
    alternate = cache.get("alternate_segments")
    if not alternate:
        raise ValueError("No alternate segmentation stored for this document.")
    if document.doc_type not in (DocType.TD, DocType.TP):
        raise ValueError("Only TD/TP segmentations can be switched.")

    # Snapshot BEFORE the delete: edited rows verbatim; hints carried by number.
    rows = (await db.execute(
        select(Exercise).where(Exercise.document_id == document.id)
    )).scalars().all()
    edited = {
        e.number_normalized: {
            "number": e.number, "statement": e.statement, "hints": e.hints,
            "hint_status": e.hint_status, "hint_source": e.hint_source,
            "concept": e.concept,
        }
        for e in rows if e.edited_by_teacher
    }
    carried = {
        e.number_normalized: {
            "hints": e.hints,
            "hint_status": (
                HintStatus.pending_review
                if e.hint_status == HintStatus.approved else e.hint_status
            ),
            "hint_source": e.hint_source,
        }
        for e in rows if e.hints and not e.edited_by_teacher
    }
    await db.execute(delete(Exercise).where(Exercise.document_id == document.id))

    for seg in alternate:
        norm = normalize_heading_number(seg.get("section"))
        snap = edited.pop(norm, None)
        if snap:
            db.add(Exercise(
                id=uuid.uuid4(), document_id=document.id,
                class_id=document.class_id, lab_id=document.lab_id,
                audience=document.audience,
                number=snap["number"], number_normalized=norm,
                statement=snap["statement"], hints=snap["hints"],
                hint_status=snap["hint_status"], hint_source=snap["hint_source"],
                concept=snap["concept"], edited_by_teacher=True,
            ))
            continue
        carry = carried.get(norm) or {}
        db.add(Exercise(
            id=uuid.uuid4(), document_id=document.id,
            class_id=document.class_id, lab_id=document.lab_id,
            audience=document.audience,
            number=seg.get("section"), number_normalized=norm,
            statement=seg.get("content") or "",
            hints=carry.get("hints"),
            hint_status=carry.get("hint_status") or HintStatus.none,
            hint_source=carry.get("hint_source"),
            edited_by_teacher=False,
        ))

    # Edited exercises whose number vanished from the chosen split: kept.
    for norm, snap in edited.items():
        db.add(Exercise(
            id=uuid.uuid4(), document_id=document.id,
            class_id=document.class_id, lab_id=document.lab_id,
            audience=document.audience,
            number=snap["number"], number_normalized=norm,
            statement=snap["statement"], hints=snap["hints"],
            hint_status=snap["hint_status"], hint_source=snap["hint_source"],
            concept=snap["concept"], edited_by_teacher=True,
        ))

    document.segmentation_cache = {
        **cache,
        "segments": alternate,
        "alternate_segments": cache["segments"],
        "chosen": which,
    }
    document.ingest_report = {
        **(document.ingest_report or {}),
        "segmentation_confirmed": True,
        "exercise_count": len(alternate),
    }
    await db.flush()
