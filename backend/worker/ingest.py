"""
ingest.py — document ingestion pipeline.

    parse → gate → clean → VLM repair → chunk → contextualize → embed → index

Every document takes the same path and produces Contextual-Retrieval chunks for
hybrid RAG. There is no document type to branch on: the pipeline used to also
build structured Exercises rows (and Answers rows from a corrigé) via an
LLM-primary segmenter with a regex cross-check, a boundary cache and numbering
reconciliation. All of that went with those tables.

Model-touching steps are injected (`embed_fn` / `context_fn` / `repair_fn`) and
the parse step is injectable (`parse_fn`) so the pipeline is testable without a
live model or a real PDF.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import DocChunk, Document, DocumentStatus
from backend.worker.chunking import chunk_pages
from backend.worker.parsing import (
    GateResult,
    character_yield_gate,
    extract_pdf_text,
    html_to_markdown,
    split_markdown_pages,
    strip_repeated_lines,
)

logger = logging.getLogger(__name__)

# [v7.3] Bounded concurrency for per-chunk Contextual-Retrieval calls.
CONTEXT_CONCURRENCY = 4

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
ContextFn = Callable[[str, str], Awaitable[str]]


def _estimate_tokens(*texts: str) -> int:
    """Rough token estimate (≈4 chars/token) for the ingest-budget observation.
    We don't have real usage from the injected context_fn, so this is an honest
    approximation — enough to spot a pathologically expensive document, never a
    billing figure."""
    return sum(max(1, len(t) // 4) for t in texts if t)
ParseFn = Callable[[str], tuple[list[str], GateResult]]

# [v8.1] VLM garbled-formula repair: (pages, storage_path) → (repaired pages,
# audit entries). Wired by the worker only when VLM_MODEL is configured.
RepairFn = Callable[[list[str], str], Awaitable[tuple[list[str], list[dict]]]]


def _default_parse(storage_path: str) -> tuple[list[str], GateResult]:
    """Parse a stored file into pages + a gate verdict.

    PDFs go through pymupdf + the character-yield gate. [v8.1] Markdown is read
    as text and split into heading pseudo-pages; the gate still runs so a junk
    file renamed .md is rejected like a garbled PDF. Other suffixes (test
    inputs) are read as a single page and skip the gate.
    """
    path = Path(storage_path)
    if path.suffix.lower() == ".pdf":
        pages = extract_pdf_text(path)
        return pages, character_yield_gate(pages)
    if path.suffix.lower() == ".md":
        pages = split_markdown_pages(path.read_text(encoding="utf-8"))
        return pages, character_yield_gate(pages)
    if path.suffix.lower() in (".html", ".htm"):
        # [v8.1] clean HTML → markdown-ish (headings, fences, recovered LaTeX),
        # then the same heading paging + gate as .md.
        pages = split_markdown_pages(html_to_markdown(path.read_text(encoding="utf-8")))
        return pages, character_yield_gate(pages)
    return [path.read_text()], GateResult(ok=True)


def _augmented(context: str | None, content: str) -> str:
    """Text fed to the vector + BM25 indexes: generated context prepended to original."""
    return f"{context}\n{content}" if context else content


def _scope_key(chunk) -> str:
    """The local scope a chunk is contextualized within: its section if the
    chunker found one, else its page — for slides, 1 page = 1 slide."""
    return chunk.section if chunk.section else f"page:{chunk.page_no}"


def _scope_texts(chunks) -> dict[str, str]:
    """Group chunk contents by scope key so Contextual Retrieval situates each
    chunk within its own section/slide rather than the whole document (which, for
    a long doc, would hallucinate context from the opening pages)."""
    groups: dict[str, list[str]] = {}
    for chunk in chunks:
        groups.setdefault(_scope_key(chunk), []).append(chunk.content)
    return {key: "\n\n".join(parts) for key, parts in groups.items()}


async def _snapshot_teacher_edits(db: AsyncSession, document_id: uuid.UUID):
    """Capture teacher-corrected chunks before the idempotent rebuild wipes them.
    They are matched back by chunk_index — teacher work is never silently
    dropped by a re-run."""
    edited_chunks = (await db.execute(
        select(DocChunk).where(
            DocChunk.document_id == document_id,
            DocChunk.edited_by_teacher.is_(True),
        )
    )).scalars().all()

    return {
        c.chunk_index: {
            "content": c.content,
            "context": c.context,
            "section": c.section,
            "embedding": list(c.embedding) if c.embedding is not None else None,
            "page_no": c.page_no,
        }
        for c in edited_chunks
    }


async def ingest_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    embed_fn: EmbedFn,
    context_fn: ContextFn | None = None,
    parse_fn: ParseFn | None = None,
    token_budget: int | None = None,
    repair_fn: RepairFn | None = None,
) -> None:
    """Parse → gate → clean → VLM repair → chunk → contextualize → embed → index."""
    doc = await db.get(Document, document_id)
    pages, gate = (parse_fn or _default_parse)(doc.storage_path)

    # Input gate: never silently ingest garbage — flag it back to the teacher.
    if not gate.ok:
        doc.status = DocumentStatus.needs_review
        doc.error_message = gate.reason
        await db.commit()
        return

    try:
        # Teacher-corrected chunks survive the rebuild.
        chunk_snaps = await _snapshot_teacher_edits(db, doc.id)

        # Idempotent: clear any artifacts from a previous run before rebuilding.
        await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))

        # Clean once, upstream: repeated headers/footers pollute every chunk.
        pages = strip_repeated_lines(pages)

        # VLM garbled-formula repair — PDFs only (md/html have nothing to
        # render) and only when the worker wired a repair_fn (VLM_MODEL
        # configured). Transcribe-only; audit entries land in ingest_report.
        vlm_entries: list[dict] = []
        if repair_fn is not None and doc.storage_path.lower().endswith(".pdf"):
            pages, vlm_entries = await repair_fn(pages, doc.storage_path)

        chunks = _dedup_chunks(chunk_pages(pages))

        # Contextual Retrieval: per-chunk context generated from the chunk's own
        # section/slide — never the whole document. Calls run concurrently
        # (bounded) — off-peak, but a long doc shouldn't take count × latency.
        scope_by_key = _scope_texts(chunks)
        tokens_est = 0
        if context_fn is not None:
            semaphore = asyncio.Semaphore(CONTEXT_CONCURRENCY)

            async def _one_context(chunk):
                async with semaphore:
                    scope = scope_by_key.get(_scope_key(chunk)) or chunk.content
                    result = await context_fn(scope, chunk.content)
                    # Observation only: estimate what this call cost (prompt =
                    # scope + chunk, completion = the context).
                    est = _estimate_tokens(scope, chunk.content, result)
                    return result, est

            pairs = list(await asyncio.gather(
                *(_one_context(chunk) for chunk in chunks)
            ))
            contexts = [c for c, _ in pairs]
            tokens_est = sum(e for _, e in pairs)
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
                chunk_index=index,
                content=chunk.content,
                context=context,
                section=chunk.section,
                embedding=embedding,
                tsv=_tsv_value(db, aug, doc.language),
                page_no=chunk.page_no,
            ))

        # The report the teacher audits: how the run went. Budget is observed,
        # never enforced — an over-budget document still indexes fully.
        doc.ingest_report = {
            "chunk_count": len(chunks),
            "ingest_tokens_est": tokens_est,
            "token_budget": token_budget,
            "over_budget": token_budget is not None and tokens_est > token_budget,
        }
        if vlm_entries:
            doc.ingest_report["vlm_repairs"] = vlm_entries

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
