"""M2-B: a teacher uploads an ODP file into the structured path.

What this guarantees:

* The bytes are checked as a real ODF presentation (ZIP paths, expansion
  limits, declared type) before anything is stored.
* The storage key is made by the server under the document's own folder. The
  uploaded filename is kept as a label only, never used as a path.
* The file is recorded in document_staged_objects and committed BEFORE it is
  written. If the request fails after the write, the cleanup sweep still finds
  the file.
* mode=new always makes a new document. It never merges with another document
  because the hash or the name is the same.
* mode=replace names the document and the generation the teacher saw. The
  course scope (class and lab) cannot change. The same content uploaded again
  to the same document returns the revision and job it already has; nothing is
  queued twice.

PDF, Markdown and HTML do not come here; they keep the legacy upload.
"""
import asyncio
import hashlib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, select

from backend.app.models import (
    DocumentRevision, DocumentSource, DocumentStagedObject, IngestionJob, JobStatus, _utcnow,
)
from backend.app.services import document_storage as storage
from backend.app.services import document_versions as versions
from backend.app.services.document_access import DocumentNotFound, owned_document
from backend.worker.odf_package import OdfLimits, OdfPackageError, open_package

TASK_KIND = 'odp_parse'
PDF_TASK_KIND = 'pdf_parse'
MAX_PDF_BYTES = 200 * 1024 * 1024
# How long an upload may take between recording its key and committing the
# source row. After that the sweep may remove the file.
UPLOAD_LEASE = timedelta(minutes=15)


class UploadRejected(ValueError):
    """The request or the file is not acceptable. The message is a safe code."""


@dataclass
class UploadResult:
    document: object
    revision: DocumentRevision
    job: IngestionJob | None
    created: bool


def max_upload_bytes(limits: OdfLimits | None = None, *, fmt: str = 'odp') -> int:
    return MAX_PDF_BYTES if fmt == 'pdf' else (limits or OdfLimits()).max_archive_bytes


def check_odp(content: bytes, limits: OdfLimits | None = None) -> str:
    """Return the SHA256 of a file that opens as an ODF presentation.

    One oversized member is accepted here: the reader records it as a gap
    instead of hiding it. A file the package reader refuses outright is
    rejected.
    """
    limits = limits or OdfLimits()
    if len(content) > limits.max_archive_bytes:
        raise UploadRejected('file_too_large')
    with tempfile.TemporaryDirectory(prefix='odp-upload-') as tmp:
        path = Path(tmp) / 'upload.odp'
        path.write_bytes(content)
        try:
            with open_package(path, limits) as package:
                if not package.has('content.xml'):
                    raise UploadRejected('invalid_odp')
                package.xml('content.xml')
                return package.sha256
        except OdfPackageError:
            raise UploadRejected('invalid_odp') from None




def check_pdf(content: bytes) -> str:
    if len(content) > MAX_PDF_BYTES:
        raise UploadRejected('file_too_large')
    if not content.startswith(b'%PDF-'):
        raise UploadRejected('invalid_pdf')
    import pymupdf
    try:
        with pymupdf.open(stream=content, filetype='pdf') as pdf:
            if pdf.needs_pass or not 0 < len(pdf) <= 150:
                raise UploadRejected('pdf_password_or_page_limit')
    except UploadRejected:
        raise
    except Exception:
        raise UploadRejected('invalid_pdf') from None
    return hashlib.sha256(content).hexdigest()

def display_name(filename: str | None) -> str:
    """The last part of the client's filename, as a label. Never a path."""
    name = PurePosixPath((filename or '').replace('\\', '/')).name.strip()
    return (name or 'document.odp')[:512]


async def _existing_upload(db, document_id, sha256):
    source = await db.scalar(select(DocumentSource).where(
        DocumentSource.document_id == document_id, DocumentSource.sha256 == sha256))
    if source is None:
        return None
    rev = await db.scalar(select(DocumentRevision).where(
        DocumentRevision.source_version_id == source.id, DocumentRevision.reason == 'upload'))
    job = await db.scalar(select(IngestionJob).where(IngestionJob.revision_id == rev.id)) if rev else None
    return rev, job


async def discard_staged(db, storage_root, key) -> None:
    """Remove a staged file nobody owns, then its ledger row. Best effort:
    whatever fails here the sweep does later."""
    try:
        await db.rollback()
        if await db.scalar(select(DocumentSource.id).where(DocumentSource.storage_key == key)):
            await db.execute(delete(DocumentStagedObject).where(DocumentStagedObject.storage_key == key))
        else:
            await asyncio.to_thread(storage.remove_object, storage_root, key)
            await db.execute(delete(DocumentStagedObject).where(DocumentStagedObject.storage_key == key))
        await db.commit()
    except Exception:  # noqa: BLE001 - the ledger row keeps the file findable
        await db.rollback()


async def _upload_structured(db, *, fmt: str, teacher_id, lab, shared: bool, filename, content: bytes, language: str,
                             storage_root, mode: str = 'new', replace_document_id=None,
                             expected_generation: int | None = None) -> UploadResult:
    versions._enabled()
    if mode not in ('new', 'replace'):
        raise UploadRejected('invalid_mode')
    if mode == 'new' and (replace_document_id is not None or expected_generation is not None):
        raise UploadRejected('replace_fields_require_replace_mode')
    if mode == 'replace' and (replace_document_id is None or expected_generation is None):
        raise UploadRejected('replace_document_id_and_expected_generation_required')
    sha256 = await asyncio.to_thread(check_pdf if fmt == 'pdf' else check_odp, content)
    name = display_name(filename)
    lab_scope = None if shared else lab.id

    if mode == 'replace':
        doc = await owned_document(db, replace_document_id, teacher_id)
        if doc.lifecycle == 'deleted':
            raise DocumentNotFound('not_found')
        if not doc.structured:
            raise versions.VersionConflict('legacy_document')
        # Replacing never moves a document to another class or lab.
        if doc.class_id != lab.class_id or doc.lab_id != lab_scope:
            raise versions.VersionConflict('scope_mismatch')
        found = await _existing_upload(db, doc.id, sha256)
        if found:
            rev, job = found
            await db.commit()
            return UploadResult(document=doc, revision=rev, job=job, created=False)
        if doc.lifecycle != 'active':
            raise versions.VersionConflict('source_unavailable')
        if doc.generation != expected_generation:
            raise versions.VersionConflict('stale_generation_or_lifecycle')
        document_id = doc.id
        await db.commit()
    else:
        document_id = uuid.uuid4()

    key = storage.source_key(document_id, uuid.uuid4(), fmt)
    db.add(DocumentStagedObject(storage_key=key, document_id=document_id, purpose='upload_source',
                                lease_expires_at=_utcnow() + UPLOAD_LEASE))
    await db.commit()
    try:
        await asyncio.to_thread(storage.write_object, storage_root, key, content, sha256=sha256)
        if mode == 'new':
            doc = await versions.new_document(db, teacher_id=teacher_id, class_id=lab.class_id,
                                              lab_id=lab_scope, filename=name, document_id=document_id)
            doc.language = language
            generation = doc.generation
        else:
            generation = expected_generation
        rev = await versions.register_source(
            db, document_id=document_id, teacher_id=teacher_id, expected_generation=generation,
            sha256=sha256, format=fmt, original_filename=name, key=key)
        source = await db.get(DocumentSource, rev.source_version_id)
        created = source.storage_key == key
        if created:
            job = IngestionJob(id=uuid.uuid4(), document_id=document_id, status=JobStatus.queued,
                               task_kind=PDF_TASK_KIND if fmt == 'pdf' else TASK_KIND,
                               source_version_id=source.id, revision_id=rev.id)
            db.add(job)
            await db.execute(delete(DocumentStagedObject).where(DocumentStagedObject.storage_key == key))
        else:
            # The same content was registered by a concurrent request first.
            job = await db.scalar(select(IngestionJob).where(IngestionJob.revision_id == rev.id))
        doc = await owned_document(db, document_id, teacher_id)
        await db.commit()
    except BaseException:
        await discard_staged(db, storage_root, key)
        raise
    if not created:
        await discard_staged(db, storage_root, key)
    return UploadResult(document=doc, revision=rev, job=job, created=created)


async def upload_odp(db, **kwargs) -> UploadResult:
    return await _upload_structured(db, fmt='odp', **kwargs)


async def upload_pdf(db, **kwargs) -> UploadResult:
    return await _upload_structured(db, fmt='pdf', **kwargs)
