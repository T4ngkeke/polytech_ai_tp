"""M3-B PDF draft index. Embed outside a transaction; publish when every page was read.

UTF-8 bytes are a conservative, deterministic budget proxy until the embedding
service exposes its tokenizer. ``token_estimate`` records that proxy, not model
usage. Exact character ranges always reconstruct the indexed block.
"""
from __future__ import annotations

import copy
import hashlib
import math
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select

from backend.app.models import (
    DocChunk, Document, DocumentBlock, DocumentPage, DocumentRevision, DocumentSource,
    IngestionJob, JobStatus, EMBEDDING_DIM, _utcnow,
)
from backend.app.services import document_versions as versions
from backend.app.services.document_cleanup import aware
from backend.app.services.retrieval_service import detect_language
from backend.worker.queue import claim_structured_job

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
EMBED_BATCH_SIZE = 64

CHUNKER_VERSION = 'm3b-utf8-byte-v1'
TARGET_BYTES = 400
HARD_BYTES = 600


class IndexConflict(ValueError):
    pass


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


def _tsv_value(db, text: str, language: str = "fr"):
    """The BM25 tsvector of a chunk (Postgres only; None elsewhere). The config
    comes from the document's language: index and query must share it."""
    from backend.app.services.retrieval_service import ts_config_for

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_tsvector(ts_config_for(language), text)
    return None


@dataclass(frozen=True)
class ChunkPart:
    start: int
    end: int
    content: str
    byte_count: int


def split_block(content: str, *, target_bytes: int = TARGET_BYTES,
                hard_bytes: int = HARD_BYTES) -> list[ChunkPart]:
    """Zero-overlap, gapless slices; prefer a nearby newline/space."""
    if not 0 < target_bytes <= hard_bytes:
        raise ValueError('invalid_chunk_budget')
    parts = []
    start = 0
    while start < len(content):
        end = start
        size = 0
        while end < len(content):
            width = len(content[end].encode('utf-8'))
            if size + width > target_bytes and end > start:
                break
            if size + width > hard_bytes:
                raise ValueError('chunk_budget_smaller_than_codepoint')
            size += width
            end += 1
            if size >= target_bytes:
                break
        if end < len(content):
            # Keep whitespace in one of the two exact slices; never strip it.
            lower = start + max(1, (end - start) // 2)
            breaks = [i + 1 for i in range(lower, end) if content[i].isspace()]
            if breaks:
                end = breaks[-1]
        if end <= start:
            raise ValueError('empty_chunk')
        part = content[start:end]
        parts.append(ChunkPart(start, end, part, len(part.encode('utf-8'))))
        start = end
    assert ''.join(part.content for part in parts) == content
    return parts


async def _snapshot(db, claim):
    doc = await db.get(Document, claim.document_id)
    rev = await db.get(DocumentRevision, claim.revision_id)
    job = await db.get(IngestionJob, claim.job_id)
    source = await db.get(DocumentSource, claim.source_version_id)
    if (doc is None or rev is None or job is None or source is None or source.format != 'pdf'
            or doc.lifecycle != 'active'
            or doc.generation != claim.generation or rev.document_id != doc.id
            or rev.source_version_id != claim.source_version_id
            or rev.publication_state != 'draft' or rev.processing_state != 'parsed'
            or job.lock_token != claim.lock_token or job.status != JobStatus.processing):
        raise IndexConflict('stale_index_claim')
    rows = (await db.execute(select(DocumentBlock, DocumentPage)
        .join(DocumentPage, (DocumentPage.revision_id == DocumentBlock.revision_id) &
                            (DocumentPage.page_id == DocumentBlock.page_id))
        .where(DocumentBlock.revision_id == rev.id)
        .order_by(DocumentPage.order_index, DocumentBlock.order_index))).all()
    if not rows or not any(b.index_state == 'searchable' and b.content for b, _ in rows):
        raise IndexConflict('no_searchable_blocks')
    snapshot = [(b.block_id, b.content, b.source_text, copy.deepcopy(b.source_ref or {}),
                 list(b.section_path or []), b.index_state, b.edited_by_teacher,
                 p.preview_page_no) for b, p in rows]
    meta = (doc.id, doc.class_id, doc.lab_id, doc.language, rev.id)
    await db.rollback()  # no read transaction is held while the embedder runs
    return meta, snapshot


def _prepare(snapshot):
    prepared = []
    for block_id, content, source_text, source_ref, section_path, index_state, edited, page_no in snapshot:
        if index_state != 'searchable' or not content:
            continue
        parts = split_block(content)
        section = ' / '.join(str(s) for s in section_path)[:512] or None
        fingerprint = hashlib.sha256((source_text if source_text is not None else content).encode()).hexdigest()
        for i, part in enumerate(parts):
            prepared.append((block_id, i, len(parts), part, source_text, source_ref,
                             fingerprint, section, edited, page_no))
    return prepared


async def _commit(db, claim, meta, prepared, embeddings, *, elapsed_seconds):
    doc, rev = await versions.lock_draft(db, document_id=claim.document_id,
        revision_id=claim.revision_id, expected_generation=claim.generation)
    job = await db.scalar(select(IngestionJob).where(IngestionJob.id == claim.job_id).with_for_update())
    if (job is None or job.lock_token != claim.lock_token or job.status != JobStatus.processing
            or job.task_kind != 'pdf_index' or job.lease_expires_at is None
            or aware(job.lease_expires_at) <= _utcnow() or rev.processing_state != 'parsed'
            or rev.source_version_id != claim.source_version_id):
        raise IndexConflict('stale_index_claim')
    if meta[:3] != (doc.id, doc.class_id, doc.lab_id) or meta[4] != rev.id:
        raise IndexConflict('tenant_or_revision_changed')
    # The teacher no longer picks the language: BM25 stems with the one the text is in.
    language = detect_language(item[3].content for item in prepared)
    # Embeddings are complete and validated before any existing draft rows go.
    await db.execute(delete(DocChunk).where(DocChunk.revision_id == rev.id))
    for idx, (item, embedding) in enumerate(zip(prepared, embeddings, strict=True)):
        block_id, part_index, part_count, part, source_text, source_ref, fingerprint, section, edited, page_no = item
        db.add(DocChunk(
            id=uuid.uuid4(), document_id=doc.id, class_id=doc.class_id, lab_id=doc.lab_id,
            revision_id=rev.id, block_id=block_id, chunk_index=idx,
            part_index=part_index, part_count=part_count, content_start=part.start,
            content_end=part.end, content=part.content, token_estimate=part.byte_count,
            chunker_version=CHUNKER_VERSION, source_text=source_text,
            source_ref=copy.deepcopy(source_ref), source_fingerprint=fingerprint,
            section=section, embedding=embedding, tsv=(_tsv_value(db, part.content, language)
                                     if db.bind.dialect.name == 'postgresql' else part.content),
            page_no=page_no, edited_by_teacher=bool(edited)))
    await db.flush()
    report = copy.deepcopy(rev.report or {})
    report['index'] = {'chunk_count': len(prepared), 'searchable_block_count': len({i[0] for i in prepared}),
                       'chunker_version': CHUNKER_VERSION, 'budget_unit': 'utf8_bytes_upper_bound',
                       'target': TARGET_BYTES, 'hard_limit': HARD_BYTES, 'overlap': 0,
                       'embedding_inputs': len(prepared), 'embedding_elapsed_seconds': round(elapsed_seconds, 3),
                       'embedding_model_tokens': None, 'language': language}
    # A page MinerU could not read is the only real problem: then the teacher
    # decides whether to publish. Otherwise the new version goes live at once.
    unread = sorted((await db.scalars(select(DocumentPage.source_slide_no).where(
        DocumentPage.revision_id == rev.id, DocumentPage.state != 'usable'))).all())
    report['index']['unread_pages'] = unread
    report['index_ready'] = True
    report['next_step'] = 'teacher_review_and_publish' if unread else 'published'
    rev.report = report
    rev.processing_state = 'partial' if unread else 'ready'
    rev.index_ready = True
    job.status, job.error_message = JobStatus.done, None
    if not unread:
        try:
            async with db.begin_nested():
                await versions.publish_revision(db, document_id=doc.id, revision_id=rev.id,
                    teacher_id=rev.created_by, expected_generation=doc.generation)
        except (versions.VersionConflict, versions.DocumentNotFound) as exc:
            # Keep the index; the teacher can still publish this version.
            report['index']['publish_error'] = str(exc)
            report['next_step'] = 'teacher_review_and_publish'
            rev.report = copy.deepcopy(report)
            rev.processing_state = 'partial'
    await db.commit()
    return len(prepared)


async def _retry(db, claim, *, max_attempts, code):
    await db.rollback()
    job = await db.scalar(select(IngestionJob).where(IngestionJob.id == claim.job_id).with_for_update())
    if job is not None and job.lock_token == claim.lock_token and job.status == JobStatus.processing:
        job.status = JobStatus.failed if claim.attempts >= max_attempts else JobStatus.queued
        job.error_message = code
        job.lease_expires_at = None
    await db.commit()


async def process_index_job(db, *, embed_fn, lease_seconds=1800, max_attempts=3) -> bool:
    """Process one queued PDF index task. Returns False when none is available."""
    claim = await claim_structured_job(db, task_kinds=('pdf_index',), lease_seconds=lease_seconds)
    if claim is None:
        return False
    try:
        meta, snapshot = await _snapshot(db, claim)
        prepared = _prepare(snapshot)
        started = time.monotonic()
        embeddings = await _embed_batches([item[3].content for item in prepared], embed_fn)
        if any(len(vector) != EMBEDDING_DIM for vector in embeddings):
            raise ValueError('embedding_dimension_mismatch')
        await _commit(db, claim, meta, prepared, embeddings,
                      elapsed_seconds=time.monotonic() - started)
    except IndexConflict as exc:
        await _retry(db, claim, max_attempts=max_attempts, code=str(exc))
    except Exception as exc:
        await _retry(db, claim, max_attempts=max_attempts, code=type(exc).__name__)
    return True
