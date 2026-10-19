"""M2-C reading endpoints for structured documents.

Teacher: /api/teacher/documents/{doc}/revisions/{rev}/...  (drafts included,
ownership checked). Student: /api/documents/{doc}/revisions/{rev}/...?session_id
(only published or superseded revisions of an active document in the session's
lab or class). Files are streamed from storage after the check; there is no
public static URL and no storage path ever leaves the server.
"""
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import get_current_user, require_teacher
from backend.app.database import get_db
from backend.app.models import DocumentAsset, DocumentPage, DocumentRevision, DocumentSource, User
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_reader as reader
from backend.app.services import document_storage as storage
from backend.app.services.document_access import DocumentNotFound, readable_asset, readable_document, readable_revision
from backend.app.services.page_render import render_page_png

teacher_router = APIRouter(prefix='/api/teacher/documents', tags=['document-reader'])
student_router = APIRouter(prefix='/api/documents', tags=['document-reader'])

NO_STORE = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}


def _not_found():
    return HTTPException(404, 'not_found')


async def _teacher_rev(db, document_id, revision_id, teacher):
    try:
        return await readable_revision(db, document_id=document_id, user_id=teacher.id,
                                       revision_id=revision_id, teacher=True)
    except DocumentNotFound:
        raise _not_found() from None


async def _student_rev(db, document_id, revision_id, user, session_id):
    try:
        return await readable_revision(db, document_id=document_id, user_id=user.id,
                                       revision_id=revision_id, session_id=session_id)
    except DocumentNotFound:
        raise _not_found() from None


def _file(asset: DocumentAsset, root) -> FileResponse:
    try:
        path = storage.resolve(root, asset.storage_key)
    except (ValueError, storage.StorageError):
        raise _not_found() from None
    if not path.is_file():
        raise HTTPException(410, 'source_unavailable')
    # FileResponse answers Range requests, which the PDF viewer uses.
    return FileResponse(path, media_type=asset.mime, headers=NO_STORE)


def _tool_error(exc: reader.ReaderError):
    status = 404 if exc.code == 'not_found' else 400
    return HTTPException(status, reader.error_envelope(exc)['error'])


# ---------------------------------------------------------------------------
# Teacher
# ---------------------------------------------------------------------------

@teacher_router.get('/{document_id}/revisions/{revision_id}/outline')
async def teacher_outline(document_id: uuid.UUID, revision_id: uuid.UUID, cursor: str | None = None,
                          page_id: uuid.UUID | None = None, teacher: User = Depends(require_teacher),
                          db: AsyncSession = Depends(get_db, scope="function")):
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    try:
        return await reader.open_document(db, rev, reader=f'teacher:{teacher.id}', page_id=page_id, cursor=cursor)
    except reader.ReaderError as exc:
        raise _tool_error(exc) from None


@teacher_router.get('/{document_id}/revisions/{revision_id}/pages/{page_id}')
async def teacher_page(document_id: uuid.UUID, revision_id: uuid.UUID, page_id: uuid.UUID,
                       teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function")):
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    try:
        return await reader.page_view(db, rev, page_id, teacher=True)
    except reader.ReaderError:
        raise _not_found() from None


@teacher_router.get('/{document_id}/revisions/{revision_id}/read')
async def teacher_read(document_id: uuid.UUID, revision_id: uuid.UUID, page_id: uuid.UUID | None = None,
                       block_id: uuid.UUID | None = None, start: int | None = Query(None, ge=0),
                       end: int | None = Query(None, ge=0), cursor: str | None = None,
                       budget: int = Query(reader.DEFAULT_BUDGET, ge=1, le=reader.MAX_BUDGET),
                       teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function")):
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    try:
        return await reader.read(db, rev, reader=f'teacher:{teacher.id}', teacher=True, page_id=page_id,
                                 block_id=block_id, start=start, end=end, cursor=cursor, budget=budget)
    except reader.ReaderError as exc:
        raise _tool_error(exc) from None


@teacher_router.get('/{document_id}/revisions/{revision_id}/navigate')
async def teacher_navigate(document_id: uuid.UUID, revision_id: uuid.UUID, anchor_block_id: uuid.UUID,
                           direction: str = 'next', unit: str = 'block', count: int = 1,
                           teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function")):
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    try:
        return await reader.navigate(db, rev, reader=f'teacher:{teacher.id}', anchor_block_id=anchor_block_id,
                                     direction=direction, unit=unit, count=count)
    except reader.ReaderError as exc:
        raise _tool_error(exc) from None


@teacher_router.get('/{document_id}/revisions/{revision_id}/grep')
async def teacher_grep(document_id: uuid.UUID, revision_id: uuid.UUID, pattern: str,
                       case_sensitive: bool = False, cursor: str | None = None,
                       teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function")):
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    try:
        return await reader.grep(db, rev, reader=f'teacher:{teacher.id}', pattern=pattern,
                                 case_sensitive=case_sensitive, cursor=cursor)
    except reader.ReaderError as exc:
        raise _tool_error(exc) from None


@teacher_router.get('/{document_id}/revisions/{revision_id}/assets/{asset_id}')
async def teacher_asset(document_id: uuid.UUID, revision_id: uuid.UUID, asset_id: uuid.UUID,
                        teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function"),
                        root: str = Depends(get_storage_root)):
    try:
        asset = await readable_asset(db, asset_id=asset_id, document_id=document_id, user_id=teacher.id,
                                     revision_id=revision_id, teacher=True)
    except DocumentNotFound:
        raise _not_found() from None
    return _file(asset, root)


@teacher_router.get('/{document_id}/revisions/{revision_id}/preview')
async def teacher_preview(document_id: uuid.UUID, revision_id: uuid.UUID,
                          teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function"),
                          root: str = Depends(get_storage_root)):
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    asset = await db.get(DocumentAsset, rev.preview_pdf_asset_id) if rev.preview_pdf_asset_id else None
    if asset is None:
        raise _not_found()
    return _file(asset, root)


@teacher_router.get('/{document_id}/revisions/{revision_id}/source')
async def teacher_source(document_id: uuid.UUID, revision_id: uuid.UUID,
                         teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db, scope="function"),
                         root: str = Depends(get_storage_root)):
    """The uploaded source file. Teachers only."""
    rev = await _teacher_rev(db, document_id, revision_id, teacher)
    source = await db.get(DocumentSource, rev.source_version_id)
    try:
        path = storage.verified_path(root, source.storage_key, source.sha256)
    except (ValueError, storage.StorageError):
        raise HTTPException(410, 'source_unavailable') from None
    return FileResponse(path, media_type='application/vnd.oasis.opendocument.presentation',
                        filename=source.original_filename, headers=NO_STORE)


# ---------------------------------------------------------------------------
# Student (and a teacher in a test session)
# ---------------------------------------------------------------------------

@student_router.get('/{document_id}/revisions/{revision_id}/pages/{page_id}')
async def student_page(document_id: uuid.UUID, revision_id: uuid.UUID, page_id: uuid.UUID,
                       session_id: uuid.UUID, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db, scope="function")):
    rev = await _student_rev(db, document_id, revision_id, user, session_id)
    try:
        view = await reader.page_view(db, rev, page_id, teacher=False)
    except reader.ReaderError:
        raise _not_found() from None
    latest = await db.scalar(select(DocumentRevision.id).where(
        DocumentRevision.document_id == document_id, DocumentRevision.publication_state == 'published'))
    view['newer_revision_available'] = latest is not None and latest != rev.id
    return view


@student_router.get('/{document_id}/revisions/{revision_id}/preview')
async def student_preview(document_id: uuid.UUID, revision_id: uuid.UUID, session_id: uuid.UUID,
                          user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db, scope="function"),
                          root: str = Depends(get_storage_root)):
    rev = await _student_rev(db, document_id, revision_id, user, session_id)
    if rev.preview_pdf_asset_id is None:
        raise _not_found()
    try:
        asset = await readable_asset(db, asset_id=rev.preview_pdf_asset_id, document_id=document_id,
                                     user_id=user.id, revision_id=revision_id, session_id=session_id)
    except DocumentNotFound:
        raise _not_found() from None
    return _file(asset, root)


@student_router.get('/{document_id}/revisions/{revision_id}/assets/{asset_id}')
async def student_asset(document_id: uuid.UUID, revision_id: uuid.UUID, asset_id: uuid.UUID,
                        session_id: uuid.UUID, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db, scope="function"), root: str = Depends(get_storage_root)):
    try:
        asset = await readable_asset(db, asset_id=asset_id, document_id=document_id, user_id=user.id,
                                     revision_id=revision_id, session_id=session_id)
    except DocumentNotFound:
        raise _not_found() from None
    return _file(asset, root)


@student_router.get('/{document_id}/revisions/{revision_id}/pages/{page_no}/image')
async def student_page_image(document_id: uuid.UUID, revision_id: uuid.UUID, page_no: int,
                             session_id: uuid.UUID, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db, scope="function"),
                             root: str = Depends(get_storage_root)):
    """One page of a revision's PDF as a PNG, for the citation preview: the
    chat draws the cited blocks' regions over it. Access as for the PDF."""
    rev = await _student_rev(db, document_id, revision_id, user, session_id)
    if rev.preview_pdf_asset_id is None:
        raise _not_found()
    page = await db.scalar(select(DocumentPage.page_id).where(
        DocumentPage.revision_id == revision_id, DocumentPage.preview_page_no == page_no))
    if page is None:
        raise _not_found()
    try:
        asset = await readable_asset(db, asset_id=rev.preview_pdf_asset_id, document_id=document_id,
                                     user_id=user.id, revision_id=revision_id, session_id=session_id)
        path = storage.resolve(root, asset.storage_key)
    except (DocumentNotFound, ValueError, storage.StorageError):
        raise _not_found() from None
    if not path.is_file():
        raise HTTPException(410, 'source_unavailable')
    try:
        image, _, _ = await asyncio.to_thread(render_page_png, path, asset.kind, page_no)
    except Exception:
        raise HTTPException(410, 'source_unavailable') from None
    return Response(content=image, media_type='image/png', headers=NO_STORE)


@student_router.get('/{document_id}/pdf')
async def student_original_pdf(document_id: uuid.UUID, session_id: uuid.UUID,
                               revision_id: uuid.UUID | None = None,
                               user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db, scope="function"),
                               root: str = Depends(get_storage_root)):
    """Serve the complete course PDF, with current session access checked anew.

    Structured citations pin a published revision; legacy PDFs use their
    immutable uploaded file. No storage path is exposed to the browser.
    """
    try:
        doc = await readable_document(db, document_id=document_id, user_id=user.id,
                                      session_id=session_id)
    except DocumentNotFound:
        raise _not_found() from None
    if doc.structured:
        if revision_id is None:
            raise _not_found()
        return await student_preview(document_id, revision_id, session_id, user, db, root)
    if revision_id is not None or not doc.filename.lower().endswith('.pdf'):
        raise _not_found()
    path = Path(doc.storage_path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise _not_found()
    if not path.is_file():
        raise HTTPException(410, 'source_unavailable')
    return FileResponse(path, media_type='application/pdf', headers=NO_STORE)
