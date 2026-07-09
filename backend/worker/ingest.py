"""
ingest.py — [v7.1→v7.3] document ingestion pipeline.

Strict split: CM produces Contextual-Retrieval chunks for hybrid RAG and never
exercises; TD/TP produce structured Exercises for agentic search and never
chunks. Exercises are built deterministically from the segmentation labels —
an LLM (`resegment_fn`) is consulted only when the regex segmentation looks
wrong (duplicate numbers = sub-questions mistaken for exercises, or zero
boundaries), and its failure keeps the regex result rather than failing the doc.

Model-touching steps are injected (`embed_fn` / `context_fn` / `resegment_fn`)
and the parse step is injectable (`parse_fn`) so the pipeline is testable
without a live model or a real PDF.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.exercise_number import normalize_exercise_number
from backend.app.models import (
    Answer, DocChunk, Document, DocumentStatus, DocType, Exercise,
)
from backend.worker.chunking import (
    Chunk,
    chunk_pages,
    detect_numbering_anomaly,
    resegment_with_llm,
)
from backend.worker.parsing import (
    GateResult,
    character_yield_gate,
    extract_pdf_text,
    strip_repeated_lines,
)
from backend.worker.routing import plan_for

logger = logging.getLogger(__name__)

# [v7.3] Bounded concurrency for per-chunk Contextual-Retrieval calls.
CONTEXT_CONCURRENCY = 4

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
ContextFn = Callable[[str, str], Awaitable[str]]
ParseFn = Callable[[str], tuple[list[str], GateResult]]
# [v7.3] Given the joined document text, name the true exercise-boundary
# heading lines (verbatim). Only called when the regex segmentation is suspect.
ResegmentFn = Callable[[str], Awaitable[list[str]]]


def _default_parse(storage_path: str) -> tuple[list[str], GateResult]:
    """Parse a stored file into pages + a gate verdict.

    PDFs go through pymupdf + the character-yield gate. Non-PDF files (test inputs)
    are read as a single page and skip the gate.
    """
    path = Path(storage_path)
    if path.suffix.lower() == ".pdf":
        pages = extract_pdf_text(path)
        return pages, character_yield_gate(pages)
    return [path.read_text()], GateResult(ok=True)


def _augmented(context: str | None, content: str) -> str:
    """Text fed to the vector + BM25 indexes: generated context prepended to original."""
    return f"{context}\n{content}" if context else content


def _scope_key(chunk) -> str:
    """[v7.2] The local scope a chunk is contextualized within: its section if the
    chunker found one (TD/TP), else its page — for CM slides, 1 page = 1 slide."""
    return chunk.section if chunk.section else f"page:{chunk.page_no}"


def _scope_texts(chunks) -> dict[str, str]:
    """Group chunk contents by scope key so Contextual Retrieval situates each
    chunk within its own section/slide rather than the whole document (which, for
    a long doc, would hallucinate context from the opening pages)."""
    groups: dict[str, list[str]] = {}
    for chunk in chunks:
        groups.setdefault(_scope_key(chunk), []).append(chunk.content)
    return {key: "\n\n".join(parts) for key, parts in groups.items()}


async def _segment_exercises_checked(
    pages: list[str],
    doc_type: DocType,
    resegment_fn: ResegmentFn | None,
    document_id: uuid.UUID,
) -> tuple[list, bool]:
    """Segment a TD/TP and sanity-check the numbering.

    Deterministic segmentation first; the LLM is consulted only when it looks
    wrong (zero boundaries, or duplicate numbers = sub-questions mistaken for
    exercises). LLM failure or an empty answer keeps the regex result.
    Returns (segments, resegmented) — the flag feeds the ingest report.
    """
    segments = chunk_pages(pages, doc_type)
    labeled = [c for c in segments if c.section]

    anomaly = detect_numbering_anomaly([c.section for c in labeled])
    suspect = not labeled or anomaly == "duplicate_numbers"
    if not (suspect and resegment_fn is not None):
        return labeled, False

    try:
        rejudged = await resegment_with_llm(pages, resegment_fn)
    except Exception as exc:
        logger.warning(
            "LLM re-segmentation failed for document %s (%s); keeping the "
            "regex segmentation.", document_id, repr(exc),
        )
        return labeled, False
    if not rejudged:
        return labeled, False
    return rejudged, True


def _find_gaps(numbers: list[int]) -> list[int]:
    """Missing numbers inside the observed range — likely missed boundaries."""
    if not numbers:
        return []
    present = set(numbers)
    return [n for n in range(min(present), max(present)) if n not in present]


async def _lab_collisions(db: AsyncSession, doc, numbers: list[int]) -> list[int]:
    """Numbers already claimed by ANOTHER document in the same lab — a
    query-time ambiguity the teacher must resolve."""
    if not numbers:
        return []
    rows = await db.execute(
        select(Exercise.number_normalized).where(
            Exercise.lab_id == doc.lab_id,
            Exercise.document_id != doc.id,
            Exercise.number_normalized.in_(numbers),
        )
    )
    return sorted({row[0] for row in rows})


async def _snapshot_teacher_edits(db: AsyncSession, document_id: uuid.UUID):
    """[v7.3] Capture teacher-corrected rows before the idempotent rebuild wipes
    them. Chunks are matched back by chunk_index, exercises by
    number_normalized; unmatched edited exercises are re-added as extra rows —
    teacher work is never silently dropped."""
    edited_chunks = (await db.execute(
        select(DocChunk).where(
            DocChunk.document_id == document_id,
            DocChunk.edited_by_teacher.is_(True),
        )
    )).scalars().all()
    edited_exercises = (await db.execute(
        select(Exercise).where(
            Exercise.document_id == document_id,
            Exercise.edited_by_teacher.is_(True),
        )
    )).scalars().all()

    chunk_snaps = {
        c.chunk_index: {
            "content": c.content,
            "context": c.context,
            "section": c.section,
            "embedding": list(c.embedding) if c.embedding is not None else None,
            "page_no": c.page_no,
        }
        for c in edited_chunks
    }
    exercise_snaps = {
        e.number_normalized: {
            "number": e.number,
            "statement": e.statement,
            "hints": e.hints,
            "concept": e.concept,
        }
        for e in edited_exercises
    }
    return chunk_snaps, exercise_snaps


async def ingest_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    embed_fn: EmbedFn,
    context_fn: ContextFn | None = None,
    parse_fn: ParseFn | None = None,
    resegment_fn: ResegmentFn | None = None,
) -> None:
    """Parse → gate → clean → segment → [CM: chunks | TD/TP: exercises]."""
    doc = await db.get(Document, document_id)
    pages, gate = (parse_fn or _default_parse)(doc.storage_path)

    # Input gate: never silently ingest garbage — flag it back to the teacher.
    if not gate.ok:
        doc.status = DocumentStatus.needs_review
        doc.error_message = gate.reason
        await db.commit()
        return

    doc_type = doc.doc_type or DocType.CM
    plan = plan_for(doc_type, has_answers=bool(doc.has_answers))

    try:
        # [v7.3] Teacher-corrected rows survive the rebuild.
        chunk_snaps, exercise_snaps = await _snapshot_teacher_edits(db, doc.id)

        # Idempotent: clear any artifacts from a previous run before rebuilding.
        await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
        await db.execute(delete(Exercise).where(Exercise.document_id == doc.id))
        await db.execute(delete(Answer).where(Answer.document_id == doc.id))

        # [v7.3] Clean once, upstream: repeated headers/footers pollute chunks
        # and exercise segments alike. Every downstream step sees cleaned pages.
        pages = strip_repeated_lines(pages)

        if plan.produce_chunks:
            chunks = _dedup_chunks(chunk_pages(pages, doc_type))

            # Contextual Retrieval: per-chunk context generated from the chunk's
            # own section/slide — never the whole document. [v7.3] Calls run
            # concurrently (bounded) — off-peak, but a long doc shouldn't take
            # chunk-count × latency.
            scope_by_key = _scope_texts(chunks)
            if plan.contextual_retrieval and context_fn is not None:
                semaphore = asyncio.Semaphore(CONTEXT_CONCURRENCY)

                async def _one_context(chunk):
                    async with semaphore:
                        scope = scope_by_key.get(_scope_key(chunk)) or chunk.content
                        return await context_fn(scope, chunk.content)

                contexts = list(await asyncio.gather(
                    *(_one_context(chunk) for chunk in chunks)
                ))
            else:
                contexts = [None] * len(chunks)

            augmented = [_augmented(ctx, c.content) for ctx, c in zip(contexts, chunks)]
            embeddings = await embed_fn(augmented) if augmented else []

            for index, (chunk, context, embedding, aug) in enumerate(
                zip(chunks, contexts, embeddings, augmented)
            ):
                snap = chunk_snaps.get(index)
                if snap:
                    # Teacher-corrected chunk: keep the corrected text (and its
                    # already-recomputed embedding from the edit endpoint).
                    db.add(DocChunk(
                        id=uuid.uuid4(),
                        document_id=doc.id,
                        class_id=doc.class_id,
                        lab_id=doc.lab_id,
                        doc_type=doc_type,
                        audience=doc.audience,
                        chunk_index=index,
                        content=snap["content"],
                        context=snap["context"],
                        section=snap["section"],
                        embedding=snap["embedding"],
                        tsv=_tsv_value(db, _augmented(snap["context"], snap["content"]),
                                       doc.language),
                        page_no=snap["page_no"],
                        edited_by_teacher=True,
                    ))
                    continue
                db.add(DocChunk(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    class_id=doc.class_id,
                    lab_id=doc.lab_id,
                    doc_type=doc_type,
                    audience=doc.audience,
                    chunk_index=index,
                    content=chunk.content,
                    context=context,
                    section=chunk.section,
                    embedding=embedding,
                    tsv=_tsv_value(db, aug, doc.language),
                    page_no=chunk.page_no,
                ))

        if plan.extract_exercises:
            # [v7.3] Deterministic build: number = the segmentation-captured
            # label, statement = the segment body. No LLM on the happy path;
            # hints are teacher-triggered later, never generated at ingest.
            segments, resegmented = await _segment_exercises_checked(
                pages, doc_type, resegment_fn, doc.id,
            )
            for segment in segments:
                norm = normalize_exercise_number(segment.section)
                snap = exercise_snaps.pop(norm, None)
                db.add(Exercise(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    class_id=doc.class_id,
                    lab_id=doc.lab_id,
                    audience=doc.audience,
                    number=snap["number"] if snap else segment.section,
                    number_normalized=norm,
                    statement=snap["statement"] if snap else segment.content,
                    hints=snap["hints"] if snap else None,
                    concept=snap["concept"] if snap else None,
                    edited_by_teacher=bool(snap),
                ))

            # Edited exercises whose number vanished from the re-ingested doc:
            # teacher work is never silently dropped.
            for norm, snap in exercise_snaps.items():
                db.add(Exercise(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    class_id=doc.class_id,
                    lab_id=doc.lab_id,
                    audience=doc.audience,
                    number=snap["number"],
                    number_normalized=norm,
                    statement=snap["statement"],
                    hints=snap["hints"],
                    concept=snap["concept"],
                    edited_by_teacher=True,
                ))

            # [v7.3] Reconciliation report — the teacher audits warnings, not
            # the whole document.
            labels = [s.section for s in segments]
            numbers = [
                n for n in (normalize_exercise_number(l) for l in labels)
                if n is not None
            ]
            doc.ingest_report = {
                "anomaly": detect_numbering_anomaly(labels),
                "gaps": _find_gaps(numbers),
                "collisions": await _lab_collisions(db, doc, numbers),
                "resegmented": resegmented,
                "exercise_count": len(segments),
            }

        if plan.produce_answers:
            # [v8.0 §9] Segment the answer-bearing document by number (a corrigé
            # or an answers-carrying TD/TP is numbered like a problem set) and
            # store one Answer row per number. answer_form (classified) and
            # exercise_id (paired) are filled later, at hint generation. This is
            # worker-only code — the student path never touches the Answers table
            # (guarded by test_answers_isolation).
            for segment in (c for c in chunk_pages(pages, doc_type) if c.section):
                db.add(Answer(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    number_raw=segment.section,
                    number_normalized=normalize_exercise_number(segment.section),
                    answer_text=segment.content,
                ))

        doc.status = DocumentStatus.indexed
        doc.page_count = len(pages)
        await db.commit()
    except Exception as exc:
        # Discard partial writes, then record the failure on the document.
        await db.rollback()
        failed = await db.get(Document, document_id)
        failed.status = DocumentStatus.failed
        failed.error_message = str(exc)
        await db.commit()
        raise


def _dedup_chunks(chunks):
    """[v7.3] Drop chunks whose content already appeared in this document —
    recurring notices below the header-strip threshold waste recall slots."""
    seen: set[str] = set()
    unique = []
    for chunk in chunks:
        key = chunk.content.strip()
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    return unique


def _tsv_value(db: AsyncSession, text: str, language: str = "fr"):
    """BM25 tsvector built at ingest time (Postgres only; None elsewhere).

    [v7.3] The config comes from the document's language — ingest and query
    sides must share it or stemming silently breaks matching."""
    from backend.app.services.retrieval_service import ts_config_for

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_tsvector(ts_config_for(language), text)
    return None
