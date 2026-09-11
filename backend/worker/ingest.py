"""
ingest.py — document ingestion pipeline.

    PDF layout (opt-in) or legacy parsing → chunks → match edits → context → embed → index

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
import hashlib
import json
import math
from collections import Counter
from dataclasses import replace
import logging
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import DocChunk, Document, DocumentStatus
from backend.worker.chunking import chunk_pages
from backend.worker.page_reader import PageReadError, ReadPage, chunk_read_pages
from backend.worker.context import clean_context, heading_context, is_heading_only
from backend.worker.parsing import (
    GateResult,
    character_yield_gate,
    extract_pdf_text,
    html_to_markdown,
    split_markdown_pages,
)

logger = logging.getLogger(__name__)

# [v7.3] Bounded concurrency for per-chunk Contextual-Retrieval calls.
CONTEXT_CONCURRENCY = 4
CONTEXT_SCOPE_CHARS = 4000
EMBED_BATCH_SIZE = 64

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
ContextFn = Callable[[str, str], Awaitable[str]]
PageReaderFn = Callable[[str], Awaitable[tuple[list[ReadPage], dict]]]


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
        pages = extract_pdf_text(path, clean_margins=True)
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


def _source_fingerprint(chunk) -> str:
    """Identify the source text and heading, independently of its current index."""
    ref = getattr(chunk, 'source_ref', None)
    if ref is not None:
        ref = {k:v for k,v in ref.items() if k != 'split_signature'}
        ref = {**ref, 'ids': sorted(ref.get('ids', [])), 'regions': sorted(ref.get('regions', []))}
        value = json.dumps([getattr(chunk, 'source_text', None), ref], ensure_ascii=False, sort_keys=True)
    elif getattr(chunk, "source_text", None) is not None:
        value = json.dumps(["source-text-v1", chunk.page_no, chunk.source_text], ensure_ascii=False)
    else:
        value = json.dumps([chunk.section, chunk.content], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()



def _context_scope(chunks, index: int) -> str:
    """Bounded local context centered on this chunk, never the start of a long page."""
    chunk = chunks[index]
    prefix = f"Section: {chunk.section}\n" if chunk.section else ""
    # Teacher edits may exceed the normal chunk limit; keep this scope bounded.
    current = chunk.content[:max(0, CONTEXT_SCOPE_CHARS - len(prefix))]
    remaining = max(0, CONTEXT_SCOPE_CHARS - len(prefix) - len(current) - 4)

    def same_scope(other):
        return other.section == chunk.section and (
            chunk.section is not None or other.page_no == chunk.page_no
        )

    before = chunks[index - 1].content if index and same_scope(chunks[index - 1]) else ""
    after = chunks[index + 1].content if index + 1 < len(chunks) and same_scope(chunks[index + 1]) else ""
    before_budget = min(len(before), remaining // 2 if after else remaining)
    before = before[-before_budget:] if before_budget else ""
    after = after[:remaining - len(before)]
    return prefix + "\n\n".join(part for part in (before, current, after) if part)


class TeacherEditConflict(ValueError):
    """An edited source cannot be matched uniquely; keep the previous index."""


async def _match_previous_chunks(db: AsyncSession, document_id: uuid.UUID, chunks):
    previous = (await db.execute(
        select(DocChunk).where(DocChunk.document_id == document_id)
    )).scalars().all()
    old_keys = [
        row.source_fingerprint or (None if row.edited_by_teacher else _source_fingerprint(row))
        for row in previous
    ]
    new_keys = [_source_fingerprint(chunk) for chunk in chunks]
    old_counts, new_counts = Counter(old_keys), Counter(new_keys)
    matched = {}
    conflicts = []
    for row, key in zip(previous, old_keys):
        if key is not None and old_counts[key] == new_counts[key] == 1:
            matched[key] = {
                "id": row.id, "content": row.content,
                "edited_by_teacher": row.edited_by_teacher,
            }
        elif row.edited_by_teacher:
            conflicts.append(str(row.id))
    if conflicts:
        raise TeacherEditConflict(
            "Re-ingestion needs review: teacher-edited chunks cannot be matched "
            "uniquely to their source. The previous chunks were kept. Chunk IDs: "
            + ", ".join(conflicts)
        )
    return new_keys, matched


class EmbeddingResultError(ValueError):
    """An embedding response is incomplete or malformed, rather than a code error."""


async def _embed_batches(texts: list[str], embed_fn: EmbedFn) -> list[list[float]]:
    """Reject incomplete/malformed results before replacing any existing chunks."""
    embeddings = []
    dimension = None
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start:start + EMBED_BATCH_SIZE]
        vectors = await embed_fn(batch)
        if len(vectors) != len(batch):
            raise EmbeddingResultError(f"Embedding count mismatch: expected {len(batch)}, got {len(vectors)}.")
        for vector in vectors:
            if not vector or any(not math.isfinite(value) for value in vector):
                raise EmbeddingResultError("Embedding contains empty or non-finite values.")
            dimension = dimension or len(vector)
            if len(vector) != dimension:
                raise EmbeddingResultError("Embedding dimensions are inconsistent.")
        embeddings.extend(vectors)
    return embeddings


async def ingest_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    embed_fn: EmbedFn,
    context_fn: ContextFn | None = None,
    parse_fn: ParseFn | None = None,
    token_budget: int | None = None,
    repair_fn: RepairFn | None = None,
    page_reader_fn: PageReaderFn | None = None,
) -> None:
    """Prepare and validate the complete replacement, then atomically rebuild the index."""
    try:
        doc = await db.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} no longer exists.")
        layout_report = None
        vlm_entries: list[dict] = []
        if page_reader_fn is not None and doc.storage_path.lower().endswith(".pdf"):
            read_pages, layout_report = await page_reader_fn(doc.storage_path)
            if 'page_results' in layout_report:
                from backend.worker.partial_ingest import ingest_pages
                await ingest_pages(db, doc, read_pages, layout_report, embed_fn=embed_fn,
                                   context_fn=context_fn, token_budget=token_budget)
                return
            pages = ["\n".join(group.text for group in page.groups) for page in read_pages]
            chunks = chunk_read_pages(read_pages)
        else:
            pages, gate = (parse_fn or _default_parse)(doc.storage_path)
            if not gate.ok:
                doc.status = DocumentStatus.needs_review
                doc.error_message = gate.reason
                await db.commit()
                return
            # The legacy repair must not rewrite native code on the layout path.
            if repair_fn is not None and doc.storage_path.lower().endswith(".pdf"):
                pages, vlm_entries = await repair_fn(pages, doc.storage_path)
            chunks = chunk_pages(pages, markdown=not doc.storage_path.lower().endswith(".pdf"))
        if not chunks:
            doc.status = DocumentStatus.needs_review
            doc.error_message = "No searchable text remained after parsing and repair."
            await db.commit()
            return
        source_keys, previous = await _match_previous_chunks(db, doc.id, chunks)
        # Context and embeddings must reflect teacher corrections too. The source
        # fingerprint continues to identify the unedited text for the next run.
        chunks = [
            replace(chunk, content=previous[key]["content"])
            if key in previous and previous[key]["edited_by_teacher"] else chunk
            for chunk, key in zip(chunks, source_keys)
        ]

        # Contextual Retrieval: per-chunk context generated from the chunk's own
        # section/slide — never the whole document. Calls run concurrently
        # (bounded) — off-peak, but a long doc shouldn't take count × latency.
        tokens_est = 0
        if context_fn is not None:
            semaphore = asyncio.Semaphore(CONTEXT_CONCURRENCY)

            async def _one_context(index, chunk):
                if is_heading_only(chunk.content):
                    return heading_context(chunk) or None, 0
                async with semaphore:
                    scope = _context_scope(chunks, index)
                    result = await context_fn(scope, chunk.content)
                    # Observation only: estimate what this call cost (prompt =
                    # scope + chunk, completion = the context).
                    est = _estimate_tokens(scope, chunk.content, result)
                    # Only the final description reaches the DB and both indexes.
                    # Invalid output falls back to a label copied from the source.
                    final = clean_context(result) or heading_context(chunk)
                    return final or None, est

            pairs = list(await asyncio.gather(
                *(_one_context(index, chunk) for index, chunk in enumerate(chunks))
            ))
            contexts = [c for c, _ in pairs]
            tokens_est = sum(e for _, e in pairs)
        else:
            contexts = [None] * len(chunks)

        augmented = [_augmented(ctx, c.content) for ctx, c in zip(contexts, chunks)]
        embeddings = await _embed_batches(augmented, embed_fn)

        # Swap only after parsing, edit matching and all model results succeeded.
        # The transaction restores the old index on any insertion/commit failure.
        await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
        for index, (chunk, context, embedding, aug, key) in enumerate(
            zip(chunks, contexts, embeddings, augmented, source_keys, strict=True)
        ):
            snap = previous.get(key)
            db.add(DocChunk(
                id=snap["id"] if snap else uuid.uuid4(),
                document_id=doc.id,
                class_id=doc.class_id,
                lab_id=doc.lab_id,
                chunk_index=index,
                content=chunk.content,
                source_fingerprint=key,
                context=context,
                section=chunk.section,
                embedding=embedding,
                tsv=_tsv_value(db, aug, doc.language),
                page_no=chunk.page_no,
                edited_by_teacher=snap["edited_by_teacher"] if snap else False,
            ))

        # The report the teacher audits: how the run went. Budget is observed,
        # never enforced — an over-budget document still indexes fully.
        doc.ingest_report = {
            "chunk_count": len(chunks),
            "ingest_tokens_est": tokens_est,
            "token_budget": token_budget,
            "over_budget": token_budget is not None and tokens_est > token_budget,
        }
        if layout_report is not None:
            doc.ingest_report["page_reader"] = layout_report
        if vlm_entries:
            doc.ingest_report["vlm_repairs"] = vlm_entries

        doc.status = DocumentStatus.indexed
        doc.error_message = None
        doc.page_count = len(pages)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        failed = await db.get(Document, document_id)
        if failed is not None:
            failed.status = (DocumentStatus.needs_review if isinstance(exc, (TeacherEditConflict, PageReadError))
                             else DocumentStatus.failed)
            failed.error_message = str(exc)
            await db.commit()
        if not isinstance(exc, (TeacherEditConflict, PageReadError)):
            raise


def _tsv_value(db: AsyncSession, text: str, language: str = "fr"):
    """BM25 tsvector built at ingest time (Postgres only; None elsewhere).

    [v7.3] The config comes from the document's language — ingest and query
    sides must share it or stemming silently breaks matching."""
    from backend.app.services.retrieval_service import ts_config_for

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_tsvector(ts_config_for(language), text)
    return None
