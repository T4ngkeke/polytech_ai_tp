"""
ingest.py — [v7.1] document ingestion pipeline.

Turns a stored document into retrieval artifacts: structure-aware chunks with
Contextual-Retrieval-augmented embeddings (for hybrid RAG) and, on the TD/TP path,
statement-only structured exercises (for agentic search).

Model-touching steps are injected (`embed_fn` / `extract_fn` / `context_fn`) and the
parse step is injectable (`parse_fn`) so the pipeline is testable without a live model
or a real PDF; in production they call the engine configured in SystemConfigs.
"""

import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import DocChunk, Document, DocumentStatus, DocType, Exercise
from backend.worker.chunking import chunk_pages
from backend.worker.parsing import GateResult, character_yield_gate, extract_pdf_text
from backend.worker.routing import plan_for

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
ExtractFn = Callable[[str], Awaitable[list[dict]]]
ContextFn = Callable[[str, str], Awaitable[str]]
ParseFn = Callable[[str], tuple[list[str], GateResult]]


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


async def ingest_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    embed_fn: EmbedFn,
    extract_fn: ExtractFn | None = None,
    context_fn: ContextFn | None = None,
    parse_fn: ParseFn | None = None,
) -> None:
    """Parse → gate → chunk → contextualize → embed → extract a document."""
    doc = await db.get(Document, document_id)
    pages, gate = (parse_fn or _default_parse)(doc.storage_path)

    # Input gate: never silently ingest garbage — flag it back to the teacher.
    if not gate.ok:
        doc.status = DocumentStatus.needs_review
        doc.error_message = gate.reason
        await db.commit()
        return

    doc_type = doc.doc_type or DocType.CM
    plan = plan_for(doc_type)

    try:
        # Idempotent: clear any artifacts from a previous run before rebuilding.
        await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
        await db.execute(delete(Exercise).where(Exercise.document_id == doc.id))

        full_text = "\n".join(pages)
        chunks = chunk_pages(pages, doc_type)

        # Contextual Retrieval: generate per-chunk context (CM path), embed augmented.
        # [v7.2] The scope is the chunk's own section/slide — not the whole document
        # — so context stays accurate and the call fits any small model on long docs.
        scope_by_key = _scope_texts(chunks)
        contexts: list[str | None] = []
        for chunk in chunks:
            if plan.contextual_retrieval and context_fn is not None:
                scope = scope_by_key.get(_scope_key(chunk)) or chunk.content
                contexts.append(await context_fn(scope, chunk.content))
            else:
                contexts.append(None)

        augmented = [_augmented(ctx, chunk.content) for ctx, chunk in zip(contexts, chunks)]
        embeddings = await embed_fn(augmented) if augmented else []

        for index, (chunk, context, embedding, aug) in enumerate(
            zip(chunks, contexts, embeddings, augmented)
        ):
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
                tsv=_tsv_value(db, aug),
                page_no=chunk.page_no,
            ))

        if plan.extract_exercises and extract_fn is not None:
            for ex in await extract_fn(full_text):
                db.add(Exercise(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    class_id=doc.class_id,
                    lab_id=doc.lab_id,
                    number=ex["number"],
                    statement=ex["statement"],
                    hints=ex.get("hints"),
                    concept=ex.get("concept"),
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


def _tsv_value(db: AsyncSession, text: str):
    """BM25 tsvector built at ingest time (Postgres only; None elsewhere)."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_tsvector("simple", text)
    return None
