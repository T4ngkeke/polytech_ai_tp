"""M2-C: teacher changes to structured documents. Caller owns the transaction.

Every change makes a new draft revision from the one the teacher looked at
(expected_revision_id + expected_generation). The old snapshot is never
changed. The new draft is "parsed": its content is stored, it has no index,
and it cannot publish until M3 builds one. Nothing from an old index is
inherited as ready.

Rules kept here, not in the router:

* A block is edited whole: content, kind, format, description.
* New content clears a model description; it may not describe old code.
* The native source text (source_text) stays as it was read, so a later
  reparse can still tell whether the source under an edit changed.
* A reparse of the same source carries a teacher change forward only when the
  block has the same identity and the same native text. Otherwise the old
  change becomes a review; it is never applied on a guess.
* A new source file never inherits teacher changes (register_source starts
  from an empty revision).
"""
import copy
import hashlib
import uuid

from sqlalchemy import select

from backend.app.models import (
    DocumentBlock, DocumentPage, DocumentReview, DocumentRevision, DocumentSource, IngestionJob,
    JobStatus, _utcnow,
)
from backend.app.services import document_versions as versions
from backend.app.services.document_access import DocumentNotFound, owned_document

BLOCK_KINDS = ('heading', 'text', 'code', 'formula', 'table', 'figure', 'navigation')
CONTENT_FORMATS = ('plain', 'markdown', 'latex')
MAX_CONTENT = 100_000
# Roadmap 6.2: descriptions that feed the index stay short. A longer one is
# refused, not cut into a sentence that may say something else.
MAX_DESCRIPTION = 400
REVIEW_KINDS = ('teacher_edit_unmatched', 'vision_description')
UNSET = object()


class EditRejected(ValueError):
    """The request cannot be applied. The message is a safe code."""


def review_key(kind: str, *parts) -> str:
    return hashlib.sha256('\x1f'.join([kind, *map(str, parts)]).encode()).hexdigest()


async def _structured(db, document_id, teacher_id):
    doc = await owned_document(db, document_id, teacher_id)
    if doc.lifecycle == 'deleted':
        raise DocumentNotFound('not_found')
    if not doc.structured:
        raise versions.VersionConflict('legacy_document')
    return doc


async def _copy_pending_reviews(db, old_rev, new_rev, *, skip=()):
    """Pending reviews of the parent still concern the same blocks of the copy."""
    rows = (await db.scalars(select(DocumentReview).where(
        DocumentReview.revision_id == old_rev.id, DocumentReview.status == 'pending'))).all()
    for row in rows:
        if row.id in skip:
            continue
        values = {c.name: copy.deepcopy(getattr(row, c.name)) for c in DocumentReview.__table__.columns}
        values.update(id=uuid.uuid4(), revision_id=new_rev.id, created_at=_utcnow())
        db.add(DocumentReview(**values))
    await db.flush()


async def _new_edit_revision(db, doc, *, teacher_id, expected_revision_id, expected_generation, note):
    parent = await db.get(DocumentRevision, expected_revision_id)
    rev = await versions.clone_revision(db, document_id=doc.id, teacher_id=teacher_id,
        expected_generation=expected_generation, expected_revision_id=expected_revision_id,
        reason='teacher_edit')
    report = copy.deepcopy(parent.report or {})
    # Whatever index the parent had is not this revision's index.
    report.update(index_ready=False, next_step='index', edited_from=str(parent.id),
                  edits=[*report.get('edits', []), note])
    report.pop('error_code', None)
    report.pop('index', None)  # the parent's index report is not this draft's index
    rev.processing_state, rev.report, rev.index_ready = 'parsed', report, False
    # A teacher's correction gets its own revision and index. The unique
    # revision-bound job is new; the old published version remains searchable.
    if (rev.reader_version or '').startswith('mineru-text-'):
        db.add(IngestionJob(id=uuid.uuid4(), document_id=doc.id,
            status=JobStatus.queued, task_kind='pdf_index',
            source_version_id=rev.source_version_id, revision_id=rev.id))
    await db.flush()
    return parent, rev


async def _block(db, rev, block_id):
    block = await db.scalar(select(DocumentBlock).where(DocumentBlock.revision_id == rev.id,
                                                        DocumentBlock.block_id == block_id))
    if block is None:
        raise DocumentNotFound('not_found')
    return block


def _index_state_after_edit(block):
    if block.index_state == 'excluded':
        return 'excluded'
    return 'searchable' if (block.content or '').strip() else 'needs_review'


def apply_block_change(block, *, teacher_id, content=UNSET, kind=UNSET, content_format=UNSET,
                       content_description=UNSET) -> list[str]:
    """Change one block in a draft copy. Returns the names of the changed fields."""
    changed = []
    if content is not UNSET:
        if not isinstance(content, str) or len(content) > MAX_CONTENT:
            raise EditRejected('invalid_content')
        if content != block.content:
            if block.source_text is None and not block.edited_by_teacher:
                # The reader leaves source_text empty when it equals the content.
                # Keep the native text before the teacher's replaces it, so a
                # reparse can still prove the source under the edit is the same.
                block.source_text = block.content
            block.content = content
            block.edited_by_teacher = True
            block.source_ref = {**(block.source_ref or {}), 'content_origin': 'teacher',
                                'edited_by': str(teacher_id)}
            changed.append('content')
            if block.description_origin == 'model':
                # A model description of the old text must not describe the new one.
                block.content_description = block.context_description = None
                block.description_origin = 'none'
    if kind is not UNSET:
        if kind not in BLOCK_KINDS:
            raise EditRejected('invalid_kind')
        if kind != block.kind:
            block.kind = kind
            block.edited_by_teacher = True
            changed.append('kind')
    if content_format is not UNSET:
        if content_format not in CONTENT_FORMATS:
            raise EditRejected('invalid_content_format')
        if content_format != block.content_format:
            block.content_format = content_format
            block.edited_by_teacher = True
            changed.append('content_format')
    if content_description is not UNSET:
        if content_description is not None and (not isinstance(content_description, str)
                                                or len(content_description) > MAX_DESCRIPTION):
            raise EditRejected('description_too_long')
        value = (content_description or '').strip() or None
        if value != block.content_description or (value and block.description_origin != 'teacher'):
            block.content_description = value
            block.description_origin = 'teacher' if value else 'none'
            changed.append('content_description')
    if changed:
        # Completeness of the source is not changed by an edit: a teacher text
        # is marked as the teacher's, it does not prove the source was complete.
        block.index_state = _index_state_after_edit(block)
    return changed


async def edit_block(db, *, document_id, teacher_id, block_id, expected_revision_id, expected_generation,
                     content=UNSET, kind=UNSET, content_format=UNSET, content_description=UNSET,
                     resolve_review=None):
    doc = await _structured(db, document_id, teacher_id)
    parent, rev = await _new_edit_revision(db, doc, teacher_id=teacher_id,
        expected_revision_id=expected_revision_id, expected_generation=expected_generation,
        note={'block_id': str(block_id), 'by': str(teacher_id), 'at': _utcnow().isoformat(), 'action': 'edit'})
    block = await _block(db, rev, block_id)
    changed = apply_block_change(block, teacher_id=teacher_id, content=content, kind=kind,
                                 content_format=content_format, content_description=content_description)
    if not changed:
        raise EditRejected('no_change')
    # JSON columns are not change-tracked in place: assign a new value.
    edits_list = [dict(e) for e in rev.report['edits']]
    edits_list[-1]['fields'] = changed
    rev.report = {**rev.report, 'edits': edits_list}
    await _copy_pending_reviews(db, parent, rev, skip={resolve_review} if resolve_review else ())
    await db.flush()
    return rev


async def set_excluded(db, *, document_id, teacher_id, block_id, expected_revision_id, expected_generation,
                       excluded: bool, reason: str | None = None):
    """"No indexing needed" (D07). The block stays readable; only search skips it."""
    doc = await _structured(db, document_id, teacher_id)
    parent, rev = await _new_edit_revision(db, doc, teacher_id=teacher_id,
        expected_revision_id=expected_revision_id, expected_generation=expected_generation,
        note={'block_id': str(block_id), 'by': str(teacher_id), 'at': _utcnow().isoformat(),
              'action': 'exclude' if excluded else 'include'})
    block = await _block(db, rev, block_id)
    if excluded == (block.index_state == 'excluded'):
        raise EditRejected('no_change')
    structure = dict(block.structure or {})
    if excluded:
        if reason is not None and len(reason) > 500:
            raise EditRejected('invalid_reason')
        block.index_state = 'excluded'
        structure['exclusion'] = {'by': str(teacher_id), 'at': _utcnow().isoformat(),
                                  'reason': (reason or '').strip() or None}
    else:
        structure.pop('exclusion', None)
        block.index_state = 'searchable' if (block.content or '').strip() else 'needs_review'
    block.structure = structure
    await _copy_pending_reviews(db, parent, rev)
    await db.flush()
    return rev


async def request_reparse(db, *, document_id, teacher_id, expected_revision_id, expected_generation,
                          block_id=None):
    """A new empty draft of the same source, and one job to parse it again.

    It never overwrites the revision it starts from, published or not. A
    request made from one block reparses the whole file: a block cannot be read
    again apart from the slide it sits on. The block is recorded as the reason.
    """
    doc = await _structured(db, document_id, teacher_id)
    rev = await versions.clone_revision(db, document_id=doc.id, teacher_id=teacher_id,
        expected_generation=expected_generation, expected_revision_id=expected_revision_id, reason='reparse')
    source = await db.get(DocumentSource, rev.source_version_id)
    if source is None or source.format not in ('odp', 'pdf'):
        raise EditRejected('reparse_not_supported')
    rev.report = {'requested_by': str(teacher_id), 'requested_at': _utcnow().isoformat(),
                  'requested_for_block': str(block_id) if block_id else None}
    job = IngestionJob(id=uuid.uuid4(), document_id=doc.id, status=JobStatus.queued, task_kind='pdf_parse' if source.format == 'pdf' else 'odp_parse',
                       source_version_id=rev.source_version_id, revision_id=rev.id)
    db.add(job)
    await db.flush()
    return rev, job


# ---------------------------------------------------------------------------
# Reparse of the same source: carry teacher changes forward, or review them
# ---------------------------------------------------------------------------

TEACHER_FIELDS = ('content', 'kind', 'content_format', 'content_description', 'context_description',
                  'description_origin', 'edited_by_teacher')


def native_text(block) -> str | None:
    """What the reader read for this block, whatever a teacher wrote since."""
    if block.source_text is not None:
        return block.source_text
    return None if block.edited_by_teacher else block.content


def _teacher_changed(block) -> bool:
    return bool(block.edited_by_teacher or block.description_origin == 'teacher'
                or block.index_state == 'excluded')


async def carry_forward(db, *, parent_revision_id, revision) -> dict:
    """Apply the parent's teacher changes to a freshly parsed reparse revision.

    Worker-side, inside the commit transaction of the new draft. A change moves
    only when the block id (source file + native object path + identity
    version) and the native text are both unchanged. Any doubt becomes a
    pending review with the old teacher text as its candidate.
    """
    parent_blocks = (await db.scalars(select(DocumentBlock).where(
        DocumentBlock.revision_id == parent_revision_id))).all()
    changed = [b for b in parent_blocks if _teacher_changed(b)]
    if not changed:
        return {'carried': 0, 'reviews': 0}
    new_blocks = {b.block_id: b for b in (await db.scalars(select(DocumentBlock).where(
        DocumentBlock.revision_id == revision.id))).all()}
    parent_pages = {p.page_id: p for p in (await db.scalars(select(DocumentPage).where(
        DocumentPage.revision_id == parent_revision_id))).all()}
    source = await db.get(DocumentSource, revision.source_version_id)
    carried = reviews = 0
    for old in changed:
        new = new_blocks.get(old.block_id)
        if new is not None and native_text(old) is not None and native_text(new) == native_text(old):
            if old.edited_by_teacher and new.source_text is None:
                new.source_text = native_text(new)
            for field in TEACHER_FIELDS:
                setattr(new, field, copy.deepcopy(getattr(old, field)))
            new.index_state = old.index_state
            new.source_ref = {**(new.source_ref or {}), **{k: v for k, v in (old.source_ref or {}).items()
                                                            if k in ('content_origin', 'edited_by')}}
            if 'exclusion' in (old.structure or {}):
                new.structure = {**(new.structure or {}), 'exclusion': old.structure['exclusion']}
            carried += 1
            continue
        page = parent_pages.get(old.page_id)
        db.add(DocumentReview(
            id=uuid.uuid4(), document_id=revision.document_id, file_sha256=source.sha256,
            source_key=review_key('teacher_edit_unmatched', old.block_id),
            source_ref={'kind': 'teacher_edit_unmatched', 'parent_revision_id': str(parent_revision_id),
                        'parent_block_id': str(old.block_id), 'old_kind': old.kind,
                        'old_description': old.content_description,
                        'old_index_state': old.index_state,
                        'why': 'block_missing' if new is None else 'native_text_changed'},
            page_no=page.source_slide_no if page else 1, bbox=[0.0, 0.0, 1.0, 1.0],
            reason=('a teacher change of the previous revision could not be matched to the same '
                    'native block after reparsing; check it against the page before applying it'),
            candidate_content=old.content if old.edited_by_teacher else None,
            status='pending', revision=1, source_version_id=revision.source_version_id,
            revision_id=revision.id, block_id=new.block_id if new is not None else None,
            kind='teacher_edit_unmatched'))
        reviews += 1
    await db.flush()
    return {'carried': carried, 'reviews': reviews}


# ---------------------------------------------------------------------------
# Structured reviews
# ---------------------------------------------------------------------------

async def list_reviews(db, *, revision_id, status=None):
    query = select(DocumentReview).where(DocumentReview.revision_id == revision_id)
    if status:
        query = query.where(DocumentReview.status == status)
    return list((await db.scalars(query.order_by(DocumentReview.page_no, DocumentReview.created_at))).all())


async def resolve_review(db, *, document_id, teacher_id, revision_id, review_id, review_revision,
                         expected_generation, status, content=None):
    """ignored: mark it. confirmed: apply it as a teacher change -> new draft.

    A vision candidate becomes the block description, never its content. A
    confirmed text equal to the candidate keeps "model" as its origin; an
    edited one is the teacher's.
    """
    doc = await _structured(db, document_id, teacher_id)
    row = await db.scalar(select(DocumentReview).where(
        DocumentReview.id == review_id, DocumentReview.document_id == doc.id,
        DocumentReview.revision_id == revision_id).with_for_update())
    if row is None:
        raise DocumentNotFound('not_found')
    if row.revision != review_revision or row.status != 'pending':
        raise versions.VersionConflict('review_revision_conflict')
    if status not in ('confirmed', 'ignored'):
        raise EditRejected('invalid_status')
    new_rev = None
    if status == 'confirmed':
        if row.block_id is None:
            raise EditRejected('review_has_no_block')
        text_value = content if content is not None else row.candidate_content
        if not isinstance(text_value, str) or not text_value.strip():
            raise EditRejected('confirmation_needs_content')
        if row.kind == 'vision_description':
            new_rev = await edit_block(db, document_id=doc.id, teacher_id=teacher_id, block_id=row.block_id,
                expected_revision_id=revision_id, expected_generation=expected_generation,
                content_description=text_value, resolve_review=row.id)
            if text_value == row.candidate_content:
                block = await _block(db, new_rev, row.block_id)
                block.description_origin = 'model'
        else:
            new_rev = await edit_block(db, document_id=doc.id, teacher_id=teacher_id, block_id=row.block_id,
                expected_revision_id=revision_id, expected_generation=expected_generation,
                content=text_value, resolve_review=row.id)
        row.final_content = text_value
    row.status, row.revision = status, row.revision + 1
    row.resolved_by, row.resolved_at = teacher_id, _utcnow()
    await db.flush()
    return row, new_rev


async def record_vision_candidate(db, *, doc, rev, block, page, result, prompt_version):
    """Store a model description as a pending candidate. Never as content."""
    source = await db.get(DocumentSource, rev.source_version_id)
    key = review_key('vision_description', block.block_id, result.model, prompt_version)
    row = await db.scalar(select(DocumentReview).where(DocumentReview.revision_id == rev.id,
                                                       DocumentReview.source_key == key).with_for_update())
    meta = {'kind': 'vision_description', 'model': result.model, 'prompt_version': prompt_version,
            'latency_ms': result.latency_ms, 'usage': result.usage,
            'usage_status': 'reported' if result.usage else 'missing',
            'uncertainties': list(result.uncertainties),
            # A relation read from a picture (an arrow, an order) is a claim to
            # check against the image, not a confirmed fact.
            'relations_verified': False, 'image_asset_id': str(result.asset_id),
            'created_at': _utcnow().isoformat()}
    if row is None:
        row = DocumentReview(
            id=uuid.uuid4(), document_id=doc.id, file_sha256=source.sha256, source_key=key,
            source_ref=meta, page_no=page.source_slide_no,
            bbox=list((block.source_ref or {}).get('bbox') or [0.0, 0.0, 1.0, 1.0]),
            reason='model description candidate; check it against the picture before using it',
            candidate_content=result.text, status='pending', revision=1,
            source_version_id=rev.source_version_id, revision_id=rev.id, block_id=block.block_id,
            kind='vision_description')
        db.add(row)
    elif row.status == 'pending':
        row.candidate_content, row.source_ref, row.revision = result.text, meta, row.revision + 1
    else:
        raise versions.VersionConflict('review_already_resolved')
    await db.flush()
    return row
