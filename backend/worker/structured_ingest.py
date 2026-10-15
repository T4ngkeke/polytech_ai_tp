"""Run one structured PDF job and store the result as a parsed draft.

The order of work, and why it is this order:

1. Claim (queue.claim_structured_job). A short transaction that records a new
   lock token, the document generation it saw, and a lease.
2. Mark the revision "processing". A short transaction under the document lock.
3. Copy the source out of storage, check its hash, and read it with MinerU.
   No transaction is open during this step, and it never touches the database.
4. Stage. Record every file key in document_staged_objects and commit, then
   write the files. A file never exists without a row that says it may be
   deleted.
5. Commit. Under the document lock, check the token, the lease, the
   generation, the revision and the lifecycle. Only then write pages, blocks
   and assets, drop the staged rows and set the revision to "parsed".

A stale result (another claim, another generation, an archived or deleted
document) writes nothing. Its files are removed straight away, or by the sweep.

The result is always a draft with index_ready false. "parsed" is not "ready",
and nothing here can publish: the same job then indexes the draft
(structured_index.py). The ODP reader and slide renderer were removed on
2026-10-15; an ODP job left in a queue fails as odp_not_supported.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select

from backend.app.models import (
    Document, DocumentAsset, DocumentBlock, DocumentBlockAsset, DocumentPage, DocumentRevision,
    DocumentSource, DocumentStagedObject, IngestionJob, JobStatus, _utcnow,
)
from backend.app.services import document_storage as storage
from backend.app.services import document_versions as versions
from backend.app.services.document_cleanup import aware, sweep_staged_objects
from backend.app.services.document_edits import carry_forward
from backend.worker.document_ir import ParsedDocument, ParseIssue
from backend.worker.pdf_reader import PDFReadError, read_pdf
from backend.worker.pdf_mineru import MinerUUnavailable, subprocess_extractor
from backend.worker.queue import DEFAULT_LEASE_SECONDS, StructuredClaim, claim_structured_job

logger = logging.getLogger(__name__)

PDF_PIPELINE_VERSION = 'm3a-pdf-1'

# IR asset kinds to database asset kinds. Written out so that a new IR kind
# fails here instead of reaching the database under a wrong name.
DB_ASSET_KIND = {
    'embedded_image': 'embedded_image',
    'formula_source': 'formula_source',
    'page_preview': 'page_preview',
    'figure_preview': 'figure_preview',
    'preview_pdf': 'pdf_preview',
}
EXTENSION = {
    'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/svg+xml': '.svg',
    'image/bmp': '.bmp', 'image/tiff': '.tif', 'image/webp': '.webp', 'image/x-wmf': '.wmf',
    'image/x-emf': '.emf', 'application/pdf': '.pdf', 'application/mathml+xml': '.mml',
}
# A block with one of these open issues, or with no text, waits for a person
# before it may be searched. Other issues stay visible on a searchable block.
REVIEW_CODES = ('extraction_failure', 'source_identity_conflict', 'content_review')


class JobFailed(Exception):
    """The source cannot be processed. Retrying would give the same answer."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class StaleClaim(Exception):
    """This worker's claim no longer allows it to write anything."""

    def __init__(self, reason: str, *, requeue: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.requeue = requeue


@dataclass(frozen=True)
class StructuredSettings:
    storage_root: Path
    task_root: Path
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_attempts: int = 3
    pdf_python: str = ""
    pdf_server_url: str = ""
    pdf_config_path: str = ""
    pdf_page_timeout_seconds: int = 240
    # Stop writing files this long before the lease ends, so the sweep never
    # removes a file that is still being written.
    write_margin_seconds: int = 120

    @classmethod
    def from_env(cls, storage_root, environ=None) -> 'StructuredSettings':
        env = environ if environ is not None else os.environ
        task_root = env.get('STRUCTURED_TASK_ROOT') or str(Path(tempfile.gettempdir()) / 'edullm-structured-tasks')
        return cls(storage_root=Path(storage_root), task_root=Path(task_root),
                   lease_seconds=int(env.get('STRUCTURED_LEASE_SECONDS') or DEFAULT_LEASE_SECONDS),
                   max_attempts=int(env.get('STRUCTURED_MAX_ATTEMPTS') or 3),
                   pdf_python=env.get('PDF_MINERU_PYTHON') or '',
                   pdf_server_url=env.get('PDF_MINERU_SERVER_URL') or '',
                   pdf_config_path=env.get('PDF_MINERU_CONFIG') or '',
                   pdf_page_timeout_seconds=int(env.get('PDF_MINERU_PAGE_TIMEOUT_SECONDS') or 240))


@dataclass
class ParseOutcome:
    document: ParsedDocument
    render: dict
    mapping: dict | None
    preview_mapping_valid: bool


def db_id(ir_id: str) -> uuid.UUID:
    """IR page and block ids are 32 hex characters: exactly one UUID."""
    return uuid.UUID(hex=ir_id)


def asset_db_id(source_version_id: uuid.UUID, ir_asset_id: str) -> uuid.UUID:
    # Asset ids are global in the database. The same file uploaded to two
    # documents has two sources, so its assets must not share an id.
    return uuid.uuid5(source_version_id, ir_asset_id)


# --------------------------------------------------------------------------
# Step 3: read the PDF. No database; MinerU is the only model.
# --------------------------------------------------------------------------

def run_pdf_pipeline(source: Path, expected_sha256: str, work_dir: Path, *,
                     settings: StructuredSettings, extractor=None) -> ParseOutcome:
    frozen_mineru = extractor is None
    if extractor is None:
        if not settings.pdf_python or not settings.pdf_server_url or not settings.pdf_config_path:
            raise JobFailed('mineru_not_configured')
        try:
            extractor = subprocess_extractor(
                python_executable=settings.pdf_python, server_url=settings.pdf_server_url,
                config_path=settings.pdf_config_path, timeout_seconds=settings.pdf_page_timeout_seconds)
        except MinerUUnavailable as exc:
            raise JobFailed(str(exc)) from exc
    model_config_sha = (hashlib.sha256(Path(settings.pdf_config_path).read_bytes()).hexdigest()
                        if frozen_mineru else None)
    try:
        document, metrics = read_pdf(source, expected_sha256, work_dir, extractor,
                                     budget_seconds=max(1, settings.lease_seconds - settings.write_margin_seconds - 300),
                                     reader_identity='pdf-mineru-text-m3a-1-' + model_config_sha[:16]
                                     if model_config_sha else 'pdf-mineru-text-m3a-1')
    except PDFReadError as exc:
        raise JobFailed(str(exc)) from exc
    if model_config_sha:
        metrics['model_config_sha256'] = model_config_sha
    metrics['mineru_version'] = '3.4.5' if frozen_mineru else None
    return ParseOutcome(document=document,
                        render={'status':'source_pdf','extractor':metrics},
                        mapping={'kind':'same_pdf_physical_page','pages':len(document.pages)},
                        preview_mapping_valid=True)


def build_report(outcome: ParseOutcome) -> dict:
    """The revision report. Counts and issues only: no notes, no file paths."""
    doc = outcome.document
    return {
        'pipeline_version': PDF_PIPELINE_VERSION,
        'schema_version': doc.schema_version,
        'reader_version': doc.reader_version,
        'identity_version': doc.identity_version,
        'config_hash': doc.config_hash,
        # parse_quality is about the parse only. It is never "failed": a file
        # that cannot be read at all fails the revision instead.
        'parse_quality': doc.parse_quality(),
        'source_sha256': doc.source_sha256,
        'source_slide_count': doc.source_slide_count,
        'visible_slide_count': len(doc.pages),
        'hidden_slide_count': len(doc.hidden_slide_numbers),
        'notes_slide_count': doc.notes_slide_count,
        'fonts': list(doc.fonts),
        'external_references': list(doc.external_references),
        'limit_hits': [dict(hit) for hit in doc.limit_hits],
        'totals': doc.totals(),
        'issues': [issue.to_dict() for issue in doc.issues],
        'render': outcome.render,
        'preview_mapping': outcome.mapping,
        'preview_mapping_valid': outcome.preview_mapping_valid,
        'source_coverage_complete': doc.parse_quality() == 'complete',
        'index_ready': False,
        'next_step': 'index',
    }


# --------------------------------------------------------------------------
# Database mapping
# --------------------------------------------------------------------------

@dataclass
class PlannedAsset:
    ir_id: str
    db_id: uuid.UUID
    kind: str
    mime: str
    sha256: str
    access_class: str
    width: int | None
    height: int | None
    payload: bytes | Path
    key: str | None = None
    reuse: bool = False


def plan_assets(outcome: ParseOutcome, claim: StructuredClaim) -> list[PlannedAsset]:
    planned = []
    for asset in outcome.document.assets:
        if asset.data is not None:
            payload = asset.data
        elif asset.kind in ('preview_pdf', 'page_preview', 'figure_preview') and asset.source_entry:
            payload = Path(asset.source_entry)  # a checked file inside this job's task directory
        else:
            raise JobFailed('asset_without_bytes')
        db_asset_id = asset_db_id(claim.source_version_id, asset.asset_id)
        planned.append(PlannedAsset(
            ir_id=asset.asset_id, db_id=db_asset_id, kind=DB_ASSET_KIND[asset.kind], mime=asset.mime,
            sha256=asset.sha256, access_class=asset.access_class, width=asset.width_px,
            height=asset.height_px, payload=payload,
            key=storage.asset_key(claim.document_id, claim.lock_token, db_asset_id,
                                  EXTENSION.get(asset.mime, '.bin'))))
    return planned


def _index_state(block) -> str:
    if not (block.content or '').strip():
        return 'needs_review'
    if any(issue.resolution == 'pending' and issue.code in REVIEW_CODES for issue in block.issues):
        return 'needs_review'
    return 'searchable'


def _page_issues(page, mapping: dict | None) -> list[dict]:
    issues = [issue.to_dict() for issue in page.issues]
    entry = next((e for e in (mapping or {}).get('entries', []) if e['page_id'] == page.page_id), None)
    if entry and entry['check'] != 'anchor_unique_match' and not any(
            i['code'] == 'preview_mapping_failed' for i in issues):
        # The reader keeps "anchor found but not unique" out of its issue list.
        # A stored page needs the reason on the page itself.
        issues.append(ParseIssue(code='preview_mapping_failed', scope='page', page_id=page.page_id,
                                 message=entry['reason'] or 'the preview page of this slide is not proven',
                                 detail={'check': entry['check']}).to_dict())
    return issues


def rows_for(outcome: ParseOutcome, claim: StructuredClaim, assets: list[PlannedAsset]):
    """Pages, blocks and block-asset links for one revision, from the IR."""
    by_ir = {asset.ir_id: asset for asset in assets}
    rev, src = claim.revision_id, claim.source_version_id
    pages, blocks, links = [], [], []
    for page in outcome.document.pages:
        preview = by_ir.get(page.preview_asset_id) if page.preview_asset_id else None
        pages.append(DocumentPage(
            revision_id=rev, source_version_id=src, page_id=db_id(page.page_id),
            order_index=page.order_index, source_slide_no=page.source_slide_no,
            preview_page_no=page.preview_page_no, title=page.title or None, state=page.state,
            issues=_page_issues(page, outcome.mapping), width=page.width_cm, height=page.height_cm,
            preview_asset_id=preview.db_id if preview else None))
        for block in page.blocks:
            blocks.append(DocumentBlock(
                revision_id=rev, source_version_id=src, block_id=db_id(block.block_id),
                page_id=db_id(page.page_id), order_index=block.order_index, kind=block.kind,
                content=block.content, content_format=block.content_format, source_text=block.source_text,
                source_ref=block.source_ref.to_dict(), section_path=list(block.section_path),
                description_origin='none', source_completeness=block.source_completeness,
                index_state=_index_state(block), issues=[issue.to_dict() for issue in block.issues],
                edited_by_teacher=False,
                structure={'table_data': block.table_data, 'model_candidate': block.model_candidate, 'group_path': list(block.group_path),
                           'preview_required': block.preview_required,
                           'preview_object_id': block.preview_object_id,
                           'preview_scope': block.preview_scope}))
            for ir_asset_id in dict.fromkeys(block.asset_ids):
                links.append(DocumentBlockAsset(
                    revision_id=rev, source_version_id=src, block_id=db_id(block.block_id),
                    asset_id=by_ir[ir_asset_id].db_id, role='primary',
                    bbox=list(block.source_ref.bbox) if block.source_ref.bbox else None))
    return pages, blocks, links


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

async def _locked(db, claim: StructuredClaim, *, now):
    """Lock the document, then the job (same order as deletion), and check the claim."""
    doc = await db.scalar(select(Document).where(Document.id == claim.document_id)
                          .with_for_update().execution_options(populate_existing=True))
    job = await db.scalar(select(IngestionJob).where(IngestionJob.id == claim.job_id)
                          .with_for_update().execution_options(populate_existing=True))
    if doc is None or doc.lifecycle == 'deleted' or job is None:
        raise StaleClaim('document_deleted')
    if job.lock_token != claim.lock_token or job.status != JobStatus.processing:
        raise StaleClaim('claim_lost')
    if aware(job.lease_expires_at) <= now:
        raise StaleClaim('lease_expired')
    if doc.lifecycle != 'active':
        # An archived document keeps its job; it runs again after restore.
        raise StaleClaim('document_archived', requeue=True)
    if doc.generation != claim.generation:
        raise StaleClaim('generation_changed', requeue=True)
    rev = await db.scalar(select(DocumentRevision).where(DocumentRevision.id == claim.revision_id)
                          .execution_options(populate_existing=True))
    if (rev is None or rev.document_id != doc.id or rev.source_version_id != claim.source_version_id
            or rev.publication_state != 'draft' or rev.processing_state not in ('queued', 'processing')):
        raise StaleClaim('revision_not_pending')
    return doc, job, rev


async def _mark_processing(db, claim: StructuredClaim, *, now) -> None:
    try:
        _, _, rev = await _locked(db, claim, now=now)
    except StaleClaim:
        await db.rollback()
        return  # the commit makes the real decision
    rev.processing_state = 'processing'
    await db.commit()


async def _source_row(db, claim: StructuredClaim) -> DocumentSource:
    source = await db.get(DocumentSource, claim.source_version_id)
    await db.commit()
    if source is not None and source.format == 'odp':
        raise JobFailed('odp_not_supported')
    if source is None or source.format != 'pdf':
        raise JobFailed('source_unavailable')
    return source


async def _stage(db, claim: StructuredClaim, planned: list[PlannedAsset], settings: StructuredSettings, now_fn):
    await _locked(db, claim, now=now_fn())
    existing = {row.id: row for row in (await db.scalars(select(DocumentAsset).where(
        DocumentAsset.source_version_id == claim.source_version_id,
        DocumentAsset.id.in_([p.db_id for p in planned])))).all()} if planned else {}
    for item in planned:
        row = existing.get(item.db_id)
        if row is not None:
            # Same source parsed again: the immutable asset is reused, never rewritten.
            if row.sha256 != item.sha256:
                raise JobFailed('source_identity_conflict')
            item.reuse = True
            continue
        db.add(DocumentStagedObject(storage_key=item.key, document_id=claim.document_id, job_id=claim.job_id,
                                    purpose='parse_asset', lease_expires_at=claim.lease_expires_at))
    await db.commit()
    stop_at = claim.lease_expires_at - timedelta(seconds=settings.write_margin_seconds)
    for item in planned:
        if item.reuse:
            continue
        if now_fn() >= stop_at:
            raise StaleClaim('lease_expired')
        await asyncio.to_thread(storage.write_object, settings.storage_root, item.key, item.payload,
                                sha256=item.sha256)


async def _commit_parsed(db, claim, outcome: ParseOutcome, planned: list[PlannedAsset], now_fn):
    doc, job, rev = await _locked(db, claim, now=now_fn())
    report = build_report(outcome)
    # Keep who asked for a reparse, and why; the parse report is added to it.
    report.update({k: v for k, v in (rev.report or {}).items() if k.startswith('requested_')})
    rev = await versions.set_processing_result(
        db, document_id=doc.id, revision_id=rev.id, expected_generation=claim.generation,
        state='parsed', report=report, index_ready=False)
    for item in planned:
        if not item.reuse:
            db.add(DocumentAsset(id=item.db_id, source_version_id=claim.source_version_id,
                                 storage_key=item.key, sha256=item.sha256, mime=item.mime,
                                 width=item.width, height=item.height, kind=item.kind,
                                 access_class=item.access_class))
    await db.flush()
    pages, blocks, links = rows_for(outcome, claim, planned)
    db.add_all(pages)
    await db.flush()
    db.add_all(blocks)
    await db.flush()
    db.add_all(links)
    await db.flush()
    if rev.reason == 'reparse' and rev.parent_revision_id:
        # M2-C: the same source parsed again. Teacher changes move only where
        # identity and native text prove it; the rest waits as reviews.
        carried = await carry_forward(db, parent_revision_id=rev.parent_revision_id, revision=rev)
        rev.report = {**rev.report, 'carried_forward': carried}
    pdf = next((item for item in planned if item.kind == 'pdf_preview'), None)
    rev.reader_version = outcome.document.reader_version
    rev.config_hash = outcome.document.config_hash
    rev.preview_pdf_asset_id = pdf.db_id if pdf else None
    staged = [item.key for item in planned if not item.reuse]
    if staged:
        await db.execute(delete(DocumentStagedObject).where(DocumentStagedObject.storage_key.in_(staged)))
    # Reuse the unique revision-bound job for indexing. A crash between
    # parse and index leaves a queued task, not a silent parsed draft.
    job.task_kind, job.status = 'pdf_index', JobStatus.queued
    job.attempts = 0
    job.lock_token = job.claimed_generation = job.lease_expires_at = None
    job.error_message = None
    await db.commit()


async def _commit_failed(db, claim, code: str, now_fn) -> None:
    doc, job, rev = await _locked(db, claim, now=now_fn())
    await versions.set_processing_result(
        db, document_id=doc.id, revision_id=rev.id, expected_generation=claim.generation,
        state='failed', report={'pipeline_version': PDF_PIPELINE_VERSION, 'error_code': code,
                                'index_ready': False})
    job.status, job.error_message = JobStatus.failed, code
    await db.commit()


async def _requeue(db, claim, *, count_attempt: bool) -> None:
    """Give the job back. A stale result does not use up one of its attempts."""
    await db.rollback()
    job = await db.scalar(select(IngestionJob).where(IngestionJob.id == claim.job_id).with_for_update()
                          .execution_options(populate_existing=True))
    if job is not None and job.lock_token == claim.lock_token and job.status == JobStatus.processing:
        job.status = JobStatus.queued
        job.lease_expires_at = None
        if not count_attempt:
            job.attempts = max(0, job.attempts - 1)
    await db.commit()


async def _discard(db, settings: StructuredSettings, planned: list[PlannedAsset], claim) -> None:
    """Remove this attempt's own files now. The keys carry this claim's token,
    so nobody else can own them. What fails here the sweep removes later."""
    await db.rollback()
    for item in planned:
        if item.reuse or item.key is None:
            continue
        try:
            owned = await db.scalar(select(DocumentAsset.id).where(DocumentAsset.storage_key == item.key))
            if not owned:
                await asyncio.to_thread(storage.remove_object, settings.storage_root, item.key)
            await db.execute(delete(DocumentStagedObject).where(
                DocumentStagedObject.storage_key == item.key, DocumentStagedObject.job_id == claim.job_id))
            await db.commit()
        except Exception:  # noqa: BLE001 - the ledger row stays for the sweep
            await db.rollback()


# --------------------------------------------------------------------------
# One job
# --------------------------------------------------------------------------

async def process_structured_job(db, *, settings: StructuredSettings, extractor=None,
                                 now_fn=_utcnow) -> bool:
    """Claim and run one structured job. Returns False when there was none.
    extractor: a MinerU stand-in for tests (None: the configured MinerU)."""
    claim = await claim_structured_job(db, lease_seconds=settings.lease_seconds, now=now_fn())
    if claim is None:
        return False
    started = time.monotonic()
    planned: list[PlannedAsset] = []
    work_dir = Path(settings.task_root) / f'{claim.job_id.hex}-{claim.lock_token.hex[:12]}'
    try:
        if claim.attempts > settings.max_attempts:
            raise JobFailed('attempts_exhausted')
        await _mark_processing(db, claim, now=now_fn())
        source = await _source_row(db, claim)
        work_dir.mkdir(parents=True, exist_ok=False)
        copy = work_dir / 'source.pdf'
        try:
            await asyncio.to_thread(storage.copy_verified, settings.storage_root, source.storage_key,
                                    source.sha256, copy)
        except storage.StorageError as exc:
            raise JobFailed(str(exc)) from exc
        outcome = await asyncio.to_thread(
            run_pdf_pipeline, copy, source.sha256, work_dir, settings=settings, extractor=extractor)
        outcome.document.source_filename = source.original_filename
        planned = plan_assets(outcome, claim)
        await _stage(db, claim, planned, settings, now_fn)
        await _commit_parsed(db, claim, outcome, planned, now_fn)
        logger.info('structured job %s parsed in %.1fs', claim.job_id, time.monotonic() - started)
    except JobFailed as exc:
        await _discard(db, settings, planned, claim)
        try:
            await _commit_failed(db, claim, exc.code, now_fn)
        except StaleClaim as stale:
            if stale.requeue:
                await _requeue(db, claim, count_attempt=False)
            else:
                await db.rollback()
    except StaleClaim as exc:
        logger.info('structured job %s: result not stored (%s)', claim.job_id, exc.reason)
        await _discard(db, settings, planned, claim)
        if exc.requeue:
            await _requeue(db, claim, count_attempt=False)
    except Exception:
        # An unexpected error is not a verdict on the file. It is retried up to
        # max_attempts; after that the revision fails as internal_error, never
        # as an issue a teacher is asked to review.
        logger.exception('structured job %s failed', claim.job_id)
        await _discard(db, settings, planned, claim)
        await _requeue(db, claim, count_attempt=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return True


async def sweep(db, settings: StructuredSettings, *, now_fn=_utcnow, limit=50) -> int:
    """Housekeeping between jobs: staged files past their lease."""
    handled = await sweep_staged_objects(db, storage_root=settings.storage_root, now=now_fn(), limit=limit)
    await db.commit()
    return handled


def sweep_task_root(task_root: Path, *, older_than_seconds: int) -> int:
    """Remove job folders left by a crashed worker. Only folders under task_root."""
    root = Path(task_root)
    if not root.is_dir():
        return 0
    removed = 0
    cutoff = time.time() - older_than_seconds
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
