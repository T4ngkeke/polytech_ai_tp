"""M1 snapshot transactions. Caller owns commit/rollback; no model/network calls.

Content writers must call lock_draft in the same transaction before adding rows.
M2 will provide the parser/upload API; these functions are the internal contract.
"""
import copy
import re
import uuid
from pathlib import PurePosixPath
from sqlalchemy import select, update, func
from backend.app.config import settings
from backend.app.models import (
    Class, Lab, User, UserRole, Document, DocumentSource, DocumentRevision,
    DocumentPage, DocumentBlock, DocumentBlockAsset, DocumentBlockLink,
    DocChunk, DocumentAsset, DocumentStatus, _utcnow,
)
from backend.app.services.document_access import owned_document, DocumentNotFound


class VersionConflict(ValueError):
    pass


def storage_key(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value or str(path) != value:
        raise ValueError('invalid_storage_key')
    return value


def _enabled():
    if not settings.STRUCTURED_INGEST_ENABLED:
        raise VersionConflict('structured_ingest_disabled')


async def new_document(db, *, teacher_id, class_id, lab_id, filename, document_id=None):
    """document_id lets an upload stage its file under the id before the row exists."""
    _enabled()
    cls = await db.scalar(select(Class).join(User, User.id == Class.teacher_id).where(
        Class.id == class_id, Class.teacher_id == teacher_id, Class.is_deleted.is_(False),
        User.is_deleted.is_(False), User.role.in_([UserRole.teacher, UserRole.admin])))
    if cls is None:
        raise DocumentNotFound('not_found')
    if lab_id is not None:
        lab = await db.scalar(select(Lab).where(Lab.id == lab_id, Lab.class_id == class_id,
                                               Lab.is_deleted.is_(False)))
        if lab is None:
            raise DocumentNotFound('not_found')
    doc = Document(id=document_id or uuid.uuid4(), class_id=class_id, lab_id=lab_id, filename=filename,
        storage_path='', content_hash='', uploaded_by=teacher_id, structured=True)
    db.add(doc)
    await db.flush()
    return doc


async def _cas(db, doc, expected_generation, *, expected_lifecycle='active', **values):
    result = await db.execute(update(Document).where(Document.id == doc.id,
        Document.generation == expected_generation, Document.lifecycle == expected_lifecycle,
        Document.structured.is_(True)).values(generation=expected_generation + 1, **values)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise VersionConflict('stale_generation_or_lifecycle')
    await db.refresh(doc)


async def lock_draft(db, *, document_id, revision_id, expected_generation):
    """Worker publication fence; locks parent before reading draft or writing rows."""
    doc = await db.scalar(select(Document).where(Document.id == document_id)
        .with_for_update().execution_options(populate_existing=True))
    if doc is None:
        raise VersionConflict('source_unavailable')
    await _cas(db, doc, expected_generation)
    rev = await db.scalar(select(DocumentRevision).where(DocumentRevision.id == revision_id,
        DocumentRevision.document_id == document_id).execution_options(populate_existing=True))
    if rev is None or rev.publication_state != 'draft':
        raise VersionConflict('immutable_revision')
    return doc, rev


async def register_source(db, *, document_id, teacher_id, expected_generation,
                          sha256, format, original_filename, key):
    """Register an already staged immutable object; no file upload or queue dispatch.

    M2 must validate the actual file and hash before calling. Key must be generated
    by the server, not taken from the uploaded filename. Repeated same-doc hash
    returns the original upload revision without generating another job/revision.
    """
    _enabled()
    storage_key(key)
    if not key.startswith(f'{document_id}/'):
        raise ValueError('storage_key_must_be_document_scoped')
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or format not in ('pdf', 'odp', 'md', 'html'):
        raise ValueError('invalid_source')
    doc = await owned_document(db, document_id, teacher_id)
    if not doc.structured or doc.lifecycle != 'active':
        raise VersionConflict('source_unavailable')
    existing = await db.scalar(select(DocumentSource).where(
        DocumentSource.document_id == doc.id, DocumentSource.sha256 == sha256))
    if existing:
        return await db.scalar(select(DocumentRevision).where(
            DocumentRevision.source_version_id == existing.id, DocumentRevision.reason == 'upload'))
    await _cas(db, doc, expected_generation)
    parent = await db.get(DocumentRevision, doc.current_revision_id) if doc.current_revision_id else None
    source = DocumentSource(id=uuid.uuid4(), document_id=doc.id, sha256=sha256, format=format,
        original_filename=original_filename, storage_key=key, created_by=teacher_id,
        replaces_source_id=parent.source_version_id if parent else None)
    db.add(source)
    await db.flush()
    rev = DocumentRevision(id=uuid.uuid4(), document_id=doc.id, source_version_id=source.id,
        parent_revision_id=doc.current_revision_id, reason='upload', created_by=teacher_id)
    db.add(rev)
    await db.flush()
    return rev


async def clone_revision(db, *, document_id, teacher_id, expected_generation,
                         expected_revision_id, reason='teacher_edit'):
    """Copy an immutable same-source snapshot; derived indices are NOT copied.

    The parent is the published revision or (M2-C) one of the teacher's drafts:
    a draft does not need a published version before it can be edited. A
    teacher edit needs a parent with stored content; a reparse may start from
    any draft, including a failed one.
    """
    _enabled()
    if reason not in ('teacher_edit', 'reparse'):
        raise ValueError('invalid_reason')
    doc = await owned_document(db, document_id, teacher_id)
    await _cas(db, doc, expected_generation)
    if expected_revision_id is None:
        raise VersionConflict('stale_revision')
    old = await db.scalar(select(DocumentRevision).where(DocumentRevision.id == expected_revision_id,
        DocumentRevision.document_id == doc.id).execution_options(populate_existing=True))
    if old is None:
        raise VersionConflict('stale_revision')
    if old.id != doc.current_revision_id:
        if old.publication_state != 'draft':
            raise VersionConflict('stale_revision')
        if reason == 'teacher_edit' and old.processing_state not in ('parsed', 'ready', 'partial'):
            raise VersionConflict('revision_has_no_content')
    rev = DocumentRevision(id=uuid.uuid4(), document_id=doc.id, source_version_id=old.source_version_id,
        parent_revision_id=old.id, reason=reason, created_by=teacher_id,
        preview_pdf_asset_id=old.preview_pdf_asset_id, reader_version=old.reader_version,
        config_hash=old.config_hash)
    db.add(rev)
    await db.flush()
    # A reparse starts empty; edits copy whole content before applying changes.
    if reason == 'teacher_edit':
        for model in (DocumentPage, DocumentBlock, DocumentBlockAsset, DocumentBlockLink):
            for row in (await db.scalars(select(model).where(model.revision_id == old.id))).all():
                values = {c.name: copy.deepcopy(getattr(row, c.name)) for c in model.__table__.columns}
                values['revision_id'] = rev.id
                db.add(model(**values))
            await db.flush()
    return rev


async def validate_publishable(db, rev, *, acknowledge_issues):
    if rev.processing_state not in ('ready', 'partial') or not rev.index_ready:
        raise VersionConflict('revision_not_ready')
    if rev.processing_state == 'partial' and not acknowledge_issues:
        raise VersionConflict('acknowledge_issues_required')
    pages = list((await db.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev.id))).all())
    blocks = list((await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id == rev.id))).all())
    chunks = list((await db.scalars(select(DocChunk).where(DocChunk.revision_id == rev.id))).all())
    if not pages or not chunks or not blocks:
        raise VersionConflict('empty_revision')
    if rev.report.get('preview_mapping_valid') is not True:
        raise VersionConflict('preview_mapping_unverified')
    # Ready means every page was read. Figures without text and blocks MinerU
    # cannot vouch for (formulas, tables, code) are normal in a PDF, not issues.
    if rev.processing_state == 'ready' and (rev.report.get('issues') or any(p.state != 'usable' for p in pages)):
        raise VersionConflict('unresolved_issues')
    for page in pages:
        if page.state != 'unavailable' and page.preview_page_no is None:
            raise VersionConflict('preview_mapping_unverified')
        if page.state != 'unavailable':
            asset = await db.get(DocumentAsset, page.preview_asset_id) if page.preview_asset_id else None
            if asset is None or asset.access_class != 'student_visible' or asset.source_version_id != rev.source_version_id:
                raise VersionConflict('preview_unavailable')
    by_id = {b.block_id: b for b in blocks}
    grouped = {}
    doc = await db.get(Document, rev.document_id)
    for chunk in chunks:
        block = by_id.get(chunk.block_id)
        if (block is None or block.index_state != 'searchable' or chunk.document_id != doc.id
                or chunk.class_id != doc.class_id or chunk.lab_id != doc.lab_id
                or chunk.content_start is None or chunk.content_end is None
                or not 0 <= chunk.content_start < chunk.content_end <= len(block.content)
                or chunk.content != block.content[chunk.content_start:chunk.content_end]
                or chunk.tsv is None):
            raise VersionConflict('invalid_index')
        grouped.setdefault(block.block_id, []).append(chunk)
    for block in blocks:
        if block.index_state != 'searchable' or not block.content:
            continue
        parts = sorted(grouped.get(block.block_id, []), key=lambda c: c.content_start)
        end = 0
        for i, part in enumerate(parts):
            if part.content_start != end or part.part_index != i or part.part_count != len(parts):
                raise VersionConflict('incomplete_index')
            end = part.content_end
        if end != len(block.content):
            raise VersionConflict('incomplete_index')


def unread_pages_message(pages) -> str:
    if not pages:
        return 'Some content could not be read.'
    return f"Not read: page{'s' if len(pages) > 1 else ''} {', '.join(map(str, pages))}. Students cannot find their content."


async def publish_revision(db, *, document_id, revision_id, teacher_id,
                           expected_generation, acknowledge_issues=False):
    doc = await owned_document(db, document_id, teacher_id)
    # Serialize first; checks cannot race an archive, deletion, or content writer.
    doc, rev = await lock_draft(db, document_id=doc.id, revision_id=revision_id,
                               expected_generation=expected_generation)
    await validate_publishable(db, rev, acknowledge_issues=acknowledge_issues)
    if doc.current_revision_id:
        old = await db.get(DocumentRevision, doc.current_revision_id)
        old.publication_state = 'superseded'
    rev.publication_state = 'published'
    rev.published_at = _utcnow()
    doc.current_revision_id = rev.id
    # The language the chunks were indexed with; searches use the document's.
    doc.language = ((rev.report or {}).get('index') or {}).get('language') or doc.language
    doc.status = DocumentStatus.indexed if rev.processing_state == "ready" else DocumentStatus.partially_indexed
    doc.error_message = (unread_pages_message(((rev.report or {}).get('index') or {}).get('unread_pages'))
                         if rev.processing_state == "partial" else None)
    doc.ingest_report = copy.deepcopy(rev.report)
    doc.page_count = await db.scalar(select(func.count()).select_from(DocumentPage).where(DocumentPage.revision_id == rev.id))
    await db.flush()
    return rev


async def set_processing_result(db, *, document_id, revision_id, expected_generation,
                                state, report, index_ready=False):
    """Trusted worker completion; ready auto-publication uses the same transaction.

    parsed (M2) means the content is stored but not indexed. It stays a draft
    with index_ready false and never publishes; M3 moves it to ready/partial.
    """
    if state not in ('parsed', 'ready', 'partial', 'failed'):
        raise ValueError('invalid_state')
    if state == 'parsed' and index_ready:
        raise ValueError('parsed_revision_is_not_indexed')
    doc, rev = await lock_draft(db, document_id=document_id, revision_id=revision_id,
                               expected_generation=expected_generation)
    rev.processing_state, rev.report, rev.index_ready = state, copy.deepcopy(report), index_ready
    await db.flush()
    if state == 'ready':
        return await publish_revision(db, document_id=doc.id, revision_id=rev.id,
            teacher_id=rev.created_by, expected_generation=doc.generation)
    return rev


async def set_archived(db, *, document_id, teacher_id, expected_generation, archived):
    doc = await owned_document(db, document_id, teacher_id)
    await _cas(db, doc, expected_generation, expected_lifecycle='active' if archived else 'archived',
               **{'lifecycle': 'archived' if archived else 'active'})
    return doc
