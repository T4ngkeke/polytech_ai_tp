"""
ingest.py — [v7] document ingestion pipeline.

Turns a stored document into retrieval artifacts: text chunks with embeddings
(for RAG) and structured exercises (for agentic search).

Embedding and extraction are injected (`embed_fn` / `extract_fn`) so the
pipeline is testable without a live model; in production they call the engine
configured in SystemConfigs.
"""

import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import DocChunk, Document, DocumentStatus, Exercise

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
ExtractFn = Callable[[str], Awaitable[list[dict]]]


def _split_into_chunks(text: str) -> list[str]:
    """Split on blank lines into non-empty paragraph chunks."""
    return [block.strip() for block in text.split("\n\n") if block.strip()]


async def ingest_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    embed_fn: EmbedFn,
    extract_fn: ExtractFn,
) -> None:
    """Chunk + embed + extract a document into DocChunks and Exercises."""
    doc = await db.get(Document, document_id)
    text = Path(doc.storage_path).read_text()

    try:
        # Idempotent: clear any artifacts from a previous run before rebuilding.
        await db.execute(delete(DocChunk).where(DocChunk.document_id == doc.id))
        await db.execute(delete(Exercise).where(Exercise.document_id == doc.id))

        chunks = _split_into_chunks(text)
        embeddings = await embed_fn(chunks)
        for index, (content, embedding) in enumerate(zip(chunks, embeddings)):
            db.add(DocChunk(
                id=uuid.uuid4(),
                document_id=doc.id,
                class_id=doc.class_id,
                lab_id=doc.lab_id,
                chunk_index=index,
                content=content,
                embedding=embedding,
            ))

        for ex in await extract_fn(text):
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
        await db.commit()
    except Exception as exc:
        # Discard partial writes, then record the failure on the document.
        await db.rollback()
        failed = await db.get(Document, document_id)
        failed.status = DocumentStatus.failed
        failed.error_message = str(exc)
        await db.commit()
        raise
