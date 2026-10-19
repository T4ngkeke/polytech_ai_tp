"""M1 teacher lifecycle endpoints; structured file uploading belongs to M2."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import User, DocumentRevision
from backend.app.services.document_access import owned_document, DocumentNotFound
from backend.app.services.document_versions import publish_revision, set_archived, VersionConflict

router = APIRouter(prefix='/api/teacher/documents', tags=['document-versions'])


class GenerationRequest(BaseModel):
    expected_generation: int = Field(ge=0)


class PublishRequest(GenerationRequest):
    acknowledge_issues: bool = False


async def require_structured(db, document_id, teacher_id):
    try:
        doc = await owned_document(db, document_id, teacher_id)
    except DocumentNotFound:
        raise HTTPException(404, 'Document not found') from None
    if doc.lifecycle == 'deleted':
        raise HTTPException(404, 'Document not found')
    if not doc.structured:
        raise HTTPException(409, 'legacy_document')
    return doc


@router.get('/{document_id}/revisions')
async def revisions(document_id: uuid.UUID, db: AsyncSession = Depends(get_db, scope="function"),
                    teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    rows = (await db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == doc.id)
                            .order_by(DocumentRevision.created_at, DocumentRevision.id))).all()
    return {'document_id': doc.id, 'generation': doc.generation, 'lifecycle': doc.lifecycle,
        'current_revision_id': doc.current_revision_id,
        'revisions': [{'id': r.id, 'source_version_id': r.source_version_id,
            'parent_revision_id': r.parent_revision_id,
            'reason': r.reason, 'processing_state': r.processing_state,
            'publication_state': r.publication_state, 'index_ready': r.index_ready,
            'created_at': r.created_at, 'published_at': r.published_at, 'report': r.report} for r in rows]}


@router.post('/{document_id}/revisions/{revision_id}/publish')
async def publish(document_id: uuid.UUID, revision_id: uuid.UUID, body: PublishRequest,
                  db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    try:
        await publish_revision(db, document_id=doc.id, revision_id=revision_id,
            teacher_id=teacher.id, **body.model_dump())
        await db.commit()
    except (VersionConflict, DocumentNotFound) as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from None
    return {'current_revision_id': doc.current_revision_id, 'generation': doc.generation}


async def archive_change(document_id, body, db, teacher, archived):
    await require_structured(db, document_id, teacher.id)
    try:
        doc = await set_archived(db, document_id=document_id, teacher_id=teacher.id,
                                expected_generation=body.expected_generation, archived=archived)
        await db.commit()
    except (VersionConflict, DocumentNotFound) as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from None
    return {'lifecycle': doc.lifecycle, 'generation': doc.generation}


@router.post('/{document_id}/archive')
async def archive(document_id: uuid.UUID, body: GenerationRequest,
                  db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    return await archive_change(document_id, body, db, teacher, True)


@router.post('/{document_id}/restore')
async def restore(document_id: uuid.UUID, body: GenerationRequest,
                  db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    return await archive_change(document_id, body, db, teacher, False)


# ---------------------------------------------------------------------------
# M2-C: whole-block edits, exclusion, reprocessing, reviews, descriptions.
# Every change names the revision the teacher saw and the generation; it makes
# a new draft and never changes the revision it came from.
# ---------------------------------------------------------------------------

from typing import Literal  # noqa: E402

from backend.app.models import DocumentAsset, DocumentBlock, DocumentBlockAsset, DocumentPage, SystemConfig  # noqa: E402
from backend.app.routers.teacher import get_storage_root  # noqa: E402
from backend.app.services import document_edits as edits  # noqa: E402
from backend.app.services import document_storage  # noqa: E402
from backend.app.services import vision_descriptions as vision  # noqa: E402
from backend.app.services.document_reader import page_region, review_view  # noqa: E402


class RevisionTarget(GenerationRequest):
    expected_revision_id: uuid.UUID


class BlockEditRequest(RevisionTarget):
    model_config = {'extra': 'forbid'}
    content: str | None = Field(default=None, max_length=edits.MAX_CONTENT)
    kind: Literal['heading', 'text', 'code', 'formula', 'table', 'figure', 'navigation'] | None = None
    content_format: Literal['plain', 'markdown', 'latex'] | None = None
    content_description: str | None = Field(default=None, max_length=edits.MAX_DESCRIPTION)
    clear_description: bool = False


class ExcludeRequest(RevisionTarget):
    excluded: bool = True
    reason: str | None = Field(default=None, max_length=500)


class ReviewResolveRequest(GenerationRequest):
    model_config = {'extra': 'forbid'}
    review_revision: int = Field(ge=1)
    status: Literal['confirmed', 'ignored']
    content: str | None = Field(default=None, max_length=edits.MAX_CONTENT)


def _revision_out(rev, doc):
    return {'revision_id': rev.id, 'parent_revision_id': rev.parent_revision_id,
            'processing_state': rev.processing_state, 'publication_state': rev.publication_state,
            'index_ready': rev.index_ready, 'generation': doc.generation}


async def _run(db, action):
    try:
        result = await action()
        await db.commit()
        return result
    except edits.EditRejected as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from None
    except DocumentNotFound:
        await db.rollback()
        raise HTTPException(404, 'not_found') from None
    except VersionConflict as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from None


@router.put('/{document_id}/blocks/{block_id}')
async def edit_block(document_id: uuid.UUID, block_id: uuid.UUID, body: BlockEditRequest,
                     db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    fields = {name: getattr(body, name) for name in ('content', 'kind', 'content_format', 'content_description')
              if getattr(body, name) is not None}
    if body.clear_description:
        fields['content_description'] = None
    rev = await _run(db, lambda: edits.edit_block(db, document_id=doc.id, teacher_id=teacher.id, block_id=block_id,
        expected_revision_id=body.expected_revision_id, expected_generation=body.expected_generation, **fields))
    return _revision_out(rev, doc)


@router.post('/{document_id}/blocks/{block_id}/exclude')
async def exclude_block(document_id: uuid.UUID, block_id: uuid.UUID, body: ExcludeRequest,
                        db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    rev = await _run(db, lambda: edits.set_excluded(db, document_id=doc.id, teacher_id=teacher.id, block_id=block_id,
        expected_revision_id=body.expected_revision_id, expected_generation=body.expected_generation,
        excluded=body.excluded, reason=body.reason))
    return _revision_out(rev, doc)


async def _reparse(db, doc, teacher, body, block_id=None):
    rev, job = await _run(db, lambda: edits.request_reparse(db, document_id=doc.id, teacher_id=teacher.id,
        expected_revision_id=body.expected_revision_id, expected_generation=body.expected_generation,
        block_id=block_id))
    return {**_revision_out(rev, doc), 'job_id': job.id}


@router.post('/{document_id}/blocks/{block_id}/reprocess')
async def reprocess_block(document_id: uuid.UUID, block_id: uuid.UUID, body: RevisionTarget,
                          db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    return await _reparse(db, doc, teacher, body, block_id)


@router.post('/{document_id}/revisions/{revision_id}/reprocess')
async def reprocess_revision(document_id: uuid.UUID, revision_id: uuid.UUID, body: GenerationRequest,
                             db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    return await _reparse(db, doc, teacher, RevisionTarget(expected_generation=body.expected_generation,
                                                           expected_revision_id=revision_id))


@router.get('/{document_id}/revisions/{revision_id}/reviews')
async def revision_reviews(document_id: uuid.UUID, revision_id: uuid.UUID, status: str | None = None,
                           db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    rev = await db.get(DocumentRevision, revision_id)
    if rev is None or rev.document_id != doc.id:
        raise HTTPException(404, 'not_found')
    return [review_view(row) for row in await edits.list_reviews(db, revision_id=rev.id, status=status)]


@router.put('/{document_id}/revisions/{revision_id}/reviews/{review_id}')
async def resolve_structured_review(document_id: uuid.UUID, revision_id: uuid.UUID, review_id: uuid.UUID,
                                    body: ReviewResolveRequest, db: AsyncSession = Depends(get_db, scope="function"),
                                    teacher: User = Depends(require_teacher)):
    doc = await require_structured(db, document_id, teacher.id)
    row, rev = await _run(db, lambda: edits.resolve_review(db, document_id=doc.id, teacher_id=teacher.id,
        revision_id=revision_id, review_id=review_id, review_revision=body.review_revision,
        expected_generation=body.expected_generation, status=body.status, content=body.content))
    return {'review': review_view(row), 'revision': _revision_out(rev, doc) if rev else None}


async def get_describe_fn(db: AsyncSession = Depends(get_db, scope="function")):
    """The verified vision client, or None when none is configured."""
    rows = (await db.scalars(select(SystemConfig))).all()
    try:
        return vision.build_describe_fn({r.key: r.value for r in rows})
    except vision.VisionUnavailable:
        return None


@router.post('/{document_id}/blocks/{block_id}/describe')
async def describe_block(document_id: uuid.UUID, block_id: uuid.UUID, body: RevisionTarget,
                         db: AsyncSession = Depends(get_db, scope="function"), teacher: User = Depends(require_teacher),
                         describe_fn=Depends(get_describe_fn), root: str = Depends(get_storage_root)):
    """One model description of one picture, stored as a candidate to check."""
    doc = await require_structured(db, document_id, teacher.id)
    if describe_fn is None:
        raise HTTPException(409, 'model_unavailable')
    rev = await db.get(DocumentRevision, body.expected_revision_id)
    if rev is None or rev.document_id != doc.id:
        raise HTTPException(404, 'not_found')
    block = await db.scalar(select(DocumentBlock).where(DocumentBlock.revision_id == rev.id,
                                                        DocumentBlock.block_id == block_id))
    if block is None:
        raise HTTPException(404, 'not_found')
    if block.kind not in ('figure', 'formula', 'table'):
        raise HTTPException(422, 'describe_needs_a_picture_block')
    page = await db.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id,
                                                      DocumentPage.page_id == block.page_id))
    image = await db.scalar(select(DocumentAsset).join(DocumentBlockAsset, DocumentBlockAsset.asset_id == DocumentAsset.id)
        .where(DocumentBlockAsset.revision_id == rev.id, DocumentBlockAsset.block_id == block.block_id,
               DocumentAsset.mime.in_(vision.IMAGE_MIMES), DocumentAsset.kind != 'page_preview').limit(1))
    whole_page = image is None
    if image is None and page.preview_asset_id:
        # No picture of its own: the checked page preview, never an unproven page.
        image = await db.get(DocumentAsset, page.preview_asset_id)
    if image is None:
        raise HTTPException(422, 'no_image_for_block')
    try:
        path = document_storage.verified_path(root, image.storage_key, image.sha256)
    except (ValueError, document_storage.StorageError):
        raise HTTPException(410, 'source_unavailable') from None
    data = path.read_bytes()
    mime, context, image_id = image.mime, {'title': page.title}, image.id
    if whole_page:
        # Only the picture, cut out of the page where MinerU found it: the model
        # describes this picture, not the whole slide.
        crop = vision.crop_region(data, mime, page_region(block.source_ref or {}))
        if crop is not None:
            data, mime = crop, 'image/png'
    if len(data) > vision.MAX_IMAGE_BYTES:
        raise HTTPException(422, 'image_too_large')
    rev_id, page_id, teacher_id = rev.id, page.page_id, teacher.id  # the rollback below expires every row
    await db.rollback()  # no transaction is held while the model answers
    try:
        result = vision.check_result(await describe_fn(data, mime, context))
    except vision.VisionRejected as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:  # noqa: BLE001 - a model or network error, reported as such
        raise HTTPException(503, 'model_unavailable') from None
    result.asset_id = image_id

    async def store():
        # Rows are read again: the teacher may have acted while the model ran.
        fresh = await require_structured(db, document_id, teacher_id)
        fresh_rev = await db.get(DocumentRevision, rev_id)
        fresh_block = await db.scalar(select(DocumentBlock).where(DocumentBlock.revision_id == rev_id,
                                                                  DocumentBlock.block_id == block_id))
        fresh_page = await db.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev_id,
                                                                DocumentPage.page_id == page_id))
        if fresh_rev is None or fresh_block is None or fresh_page is None:
            raise DocumentNotFound('not_found')
        return await edits.record_vision_candidate(db, doc=fresh, rev=fresh_rev, block=fresh_block, page=fresh_page,
                                                   result=result, prompt_version=vision.PROMPT_VERSION)
    row = await _run(db, store)
    return review_view(row)
