"""M4-A: scoped research tools over published structured PDF snapshots.

This module does not ask a model to authorize a document. The server creates a
ResearchScope from a live session, fixes its revision map, and rechecks access
at every call. Existing reading services provide ordering, ranges and cursors.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Awaitable, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator
from sqlalchemy import select, tuple_

from backend.app.models import (
    Class, ClassStudent, Document, DocumentAsset, DocumentBlock, DocumentBlockAsset, DocumentPage,
    DocumentRevision, DocumentSource, Lab, Session, User,
)
from backend.app.services import document_reader as reader
from backend.app.services.document_access import DocumentNotFound, readable_asset, readable_revision
from backend.app.services.retrieval_service import RerankFn, hybrid_search

EmbedFn = Callable[[str], Awaitable[list[float]]]
Kind = Literal['heading', 'text', 'code', 'formula', 'table', 'figure', 'navigation']


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SearchArgs(Args):
    query: StrictStr = Field(min_length=1, max_length=500)
    document_id: uuid.UUID | None = None
    kind: Kind | None = None
    top_k: int = Field(default=6, ge=1, le=12, strict=True)


class OpenArgs(Args):
    document_id: uuid.UUID
    page_id: uuid.UUID | None = None
    block_id: uuid.UUID | None = None
    cursor: StrictStr | None = None

    @model_validator(mode='after')
    def anchor_or_cursor(self):
        if self.cursor and (self.page_id or self.block_id):
            raise ValueError('cursor_and_anchor')
        if self.page_id and self.block_id:
            raise ValueError('multiple_anchors')
        return self


class NavigateArgs(Args):
    document_id: uuid.UUID
    anchor_block_id: uuid.UUID
    direction: Literal['next', 'previous']
    unit: Literal['block', 'page'] = 'block'
    count: int = Field(default=1, ge=1, le=3, strict=True)


class ReadArgs(Args):
    document_id: uuid.UUID
    page_id: uuid.UUID | None = None
    block_id: uuid.UUID | None = None
    start: int | None = Field(default=None, ge=0, strict=True)
    end: int | None = Field(default=None, ge=0, strict=True)
    cursor: StrictStr | None = None
    budget: int = Field(default=2000, ge=1, le=reader.MAX_BUDGET, strict=True)

    @model_validator(mode='after')
    def target_and_range(self):
        if (self.page_id is None) == (self.block_id is None):
            raise ValueError('exactly_one_read_target')
        if self.cursor and (self.start is not None or self.end is not None):
            raise ValueError('cursor_and_range')
        if self.page_id and (self.start is not None or self.end is not None):
            raise ValueError('page_range_not_supported')
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError('invalid_range')
        return self


class GrepArgs(Args):
    document_id: uuid.UUID
    pattern: StrictStr = Field(min_length=1, max_length=200)
    case_sensitive: StrictBool = False
    cursor: StrictStr | None = None


class ViewImageArgs(Args):
    asset_id: uuid.UUID
    page_id: uuid.UUID | None = None
    question: StrictStr = Field(min_length=1, max_length=500)


MAX_SEARCH_OUTPUT_CHARS = 8000

SCHEMAS: dict[str, type[Args]] = {
    'search': SearchArgs, 'open': OpenArgs, 'navigate': NavigateArgs,
    'read': ReadArgs, 'grep': GrepArgs, 'view_image': ViewImageArgs,
}


@dataclass(frozen=True)
class ResearchScope:
    user_id: uuid.UUID
    session_id: uuid.UUID
    run_id: uuid.UUID
    class_id: uuid.UUID
    lab_id: uuid.UUID
    snapshot_revisions: Mapping[uuid.UUID, uuid.UUID]

    @property
    def reader_id(self) -> str:
        # document_reader signs this identity into every continuation cursor.
        return f'agent:{self.user_id}:{self.session_id}:{self.run_id}'


async def _live_session(db, user_id: uuid.UUID, session_id: uuid.UUID):
    row = (await db.execute(select(Session, Lab, Class, User)
        .join(Lab, Session.lab_id == Lab.id)
        .join(Class, Lab.class_id == Class.id)
        .join(User, Session.user_id == User.id)
        .where(Session.id == session_id, Session.user_id == user_id,
               Session.is_deleted.is_(False), Lab.is_active.is_(True),
               Lab.is_deleted.is_(False), Class.is_deleted.is_(False),
               User.is_deleted.is_(False)))).first()
    if row is None:
        raise DocumentNotFound('not_found')
    chat, lab, cls, _user = row
    # Query the table on every call: db.get may reuse a stale identity-map row
    # after membership was removed by another request.
    member = await db.scalar(select(ClassStudent).where(
        ClassStudent.class_id == cls.id, ClassStudent.student_id == user_id))
    if member is None and not (chat.is_test and cls.teacher_id == user_id):
        raise DocumentNotFound('not_found')
    return lab, cls


async def create_scope(db, *, user_id: uuid.UUID, session_id: uuid.UUID,
                       run_id: uuid.UUID | None = None) -> ResearchScope:
    """Freeze only current, published PDF revisions in this live session."""
    lab, cls = await _live_session(db, user_id, session_id)
    rows = (await db.execute(select(Document.id, DocumentRevision.id)
        .join(DocumentRevision, (DocumentRevision.document_id == Document.id) &
                                (DocumentRevision.id == Document.current_revision_id))
        .join(DocumentSource, DocumentSource.id == DocumentRevision.source_version_id)
        .where(Document.class_id == cls.id,
               (Document.lab_id == lab.id) | Document.lab_id.is_(None),
               Document.lifecycle == 'active', Document.structured.is_(True),
               DocumentRevision.publication_state == 'published',
               DocumentRevision.index_ready.is_(True),
               DocumentSource.format == 'pdf'))).all()
    return ResearchScope(user_id, session_id, run_id or uuid.uuid4(), cls.id, lab.id,
                         MappingProxyType(dict(rows)))


class ResearchTools:
    def __init__(self, db, scope: ResearchScope, *, embed_fn: EmbedFn | None = None,
                 rerank_fn: RerankFn | None = None, vision_fn=None, storage_root=None):
        self.db, self.scope = db, scope
        self.embed_fn, self.rerank_fn = embed_fn, rerank_fn
        self.vision_fn, self.storage_root = vision_fn, storage_root

    async def _check_session(self):
        lab, cls = await _live_session(self.db, self.scope.user_id, self.scope.session_id)
        if lab.id != self.scope.lab_id or cls.id != self.scope.class_id:
            raise DocumentNotFound('not_found')

    async def _revision(self, document_id: uuid.UUID):
        await self._check_session()
        revision_id = self.scope.snapshot_revisions.get(document_id)
        if revision_id is None:
            raise DocumentNotFound('not_found')
        return await readable_revision(
            self.db, document_id=document_id, revision_id=revision_id,
            user_id=self.scope.user_id, session_id=self.scope.session_id,
        )

    async def page_index(self) -> dict:
        """Readable documents of the scope: filename and pages in reading order.

        Lets a model-facing view name pages by PDF number and give the previous
        and next page of a read. Unreadable documents are left out.
        """
        index = {}
        for document_id, revision_id in self.scope.snapshot_revisions.items():
            try:
                await self._revision(document_id)
            except DocumentNotFound:
                continue
            document = await self.db.get(Document, document_id)
            pages = (await self.db.scalars(select(DocumentPage)
                .where(DocumentPage.revision_id == revision_id)
                .order_by(DocumentPage.order_index))).all()
            index[str(document_id)] = {'filename': document.filename, 'pages': [
                {'page_id': str(p.page_id), 'pdf_page_no': p.preview_page_no} for p in pages]}
        return index

    async def execute(self, name: str, arguments: dict) -> dict:
        """One validated model action; an error is never treated as evidence."""
        schema = SCHEMAS.get(name)
        if schema is None:
            return reader.error_envelope(reader.ReaderError('invalid_arguments'))
        try:
            args = schema.model_validate(arguments)
            if name == 'search':
                return await self.search(args)
            if name == 'view_image':
                return await self.view_image(args)
            rev = await self._revision(args.document_id)
            if name == 'open':
                return await reader.open_document(
                    self.db, rev, reader=self.scope.reader_id,
                    page_id=args.page_id, block_id=args.block_id, cursor=args.cursor)
            if name == 'navigate':
                return await reader.navigate(
                    self.db, rev, reader=self.scope.reader_id,
                    anchor_block_id=args.anchor_block_id, direction=args.direction,
                    unit=args.unit, count=args.count)
            if name == 'read':
                return await reader.read(
                    self.db, rev, reader=self.scope.reader_id, teacher=False,
                    page_id=args.page_id, block_id=args.block_id,
                    start=args.start, end=args.end, cursor=args.cursor, budget=args.budget)
            if name == 'grep':
                return await reader.grep(
                    self.db, rev, reader=self.scope.reader_id,
                    pattern=args.pattern, case_sensitive=args.case_sensitive, cursor=args.cursor)
        except ValidationError:
            return reader.error_envelope(reader.ReaderError('invalid_arguments'))
        except DocumentNotFound:
            return reader.error_envelope(reader.ReaderError('not_found'))
        except reader.ReaderError as exc:
            return reader.error_envelope(exc)
        raise AssertionError('unhandled research tool')

    async def search(self, args: SearchArgs) -> dict:
        await self._check_session()
        if args.document_id is not None and args.document_id not in self.scope.snapshot_revisions:
            raise DocumentNotFound('not_found')
        issues = []
        embedding = None
        if self.embed_fn is not None:
            try:
                embedding = await self.embed_fn(args.query)
            except Exception:  # model failure degrades to lexical recall, visibly
                issues.append('embedding_unavailable')
        else:
            issues.append('embedding_unavailable')
        # A network embedding call may take long enough for access to change.
        await self._check_session()
        hits, all_filtered = await hybrid_search(
            self.db, args.query, embedding, self.scope.lab_id,
            class_id=self.scope.class_id,
            snapshot_revisions=self.scope.snapshot_revisions,
            document_id=args.document_id, kind=args.kind,
            top_k=args.top_k,
            rerank_fn=self.rerank_fn, language="auto",
        )
        # A reranker may have awaited a model after the SQL query. Recheck live
        # permissions before any retrieved text leaves this tool.
        await self._check_session()
        for document_id in {h.document_id for h in hits}:
            await self._revision(document_id)
        if not hits:
            return reader.envelope([], issues=issues, all_filtered=all_filtered)
        pairs = {(h.revision_id, h.block_id) for h in hits}
        blocks = (await self.db.scalars(select(DocumentBlock).where(
            tuple_(DocumentBlock.revision_id, DocumentBlock.block_id).in_(pairs)))).all()
        by_block = {(b.revision_id, b.block_id): b for b in blocks}
        page_pairs = {(b.revision_id, b.page_id) for b in blocks}
        pages = (await self.db.scalars(select(DocumentPage).where(
            tuple_(DocumentPage.revision_id, DocumentPage.page_id).in_(page_pairs)))).all()
        by_page = {(p.revision_id, p.page_id): p for p in pages}
        items_by_block: dict[tuple, dict] = {}
        truncated = False
        for hit in hits:
            block = by_block.get((hit.revision_id, hit.block_id))
            page = by_page.get((hit.revision_id, block.page_id)) if block else None
            if (not block or not page or block.index_state != 'searchable' or
                    hit.content_start is None or hit.content_end is None or
                    block.content[hit.content_start:hit.content_end] != hit.content or
                    page.preview_page_no != hit.page_no):
                issues.append('source_identity_conflict')
                continue
            key = (hit.document_id, hit.revision_id, hit.block_id)
            item = items_by_block.get(key)
            if item is None:
                item = {
                    'document_id': str(hit.document_id), 'revision_id': str(hit.revision_id),
                    'page_id': str(page.page_id), 'pdf_page_no': page.preview_page_no,
                    'block_id': str(hit.block_id), 'type': block.kind,
                    'source_completeness': block.source_completeness,
                    'issues': block.issues or [], 'snippets': [],
                }
                items_by_block[key] = item
            item['snippets'].append({
                'chunk_id': str(hit.id),
                'range': {'start': hit.content_start, 'end': hit.content_end},
                'content': hit.content,
            })
            if len(json.dumps(list(items_by_block.values()), ensure_ascii=False)) > MAX_SEARCH_OUTPUT_CHARS:
                item['snippets'].pop()
                if not item['snippets']:
                    del items_by_block[key]
                issues.append('output_budget_exhausted')
                truncated = True
                break
        items = list(items_by_block.values())
        return reader.envelope(items, issues=issues, truncated=truncated,
                               budget_units=len(json.dumps(items, ensure_ascii=False)),
                               all_filtered=all_filtered)

    async def view_image(self, args: ViewImageArgs) -> dict:
        import asyncio
        from backend.app.config import settings
        from backend.app.services import document_storage
        from backend.app.agent.result import ResearchQuotaExceeded, ResearchRunStopped
        from backend.app.services.research_vision import VisionError

        await self._check_session()
        source = (await self.db.execute(select(DocumentSource.document_id)
            .join(DocumentAsset, DocumentAsset.source_version_id == DocumentSource.id)
            .where(DocumentAsset.id == args.asset_id))).scalar_one_or_none()
        if source is None or source not in self.scope.snapshot_revisions:
            raise DocumentNotFound('not_found')
        revision_id = self.scope.snapshot_revisions[source]
        asset = await readable_asset(self.db, asset_id=args.asset_id,
            document_id=source, revision_id=revision_id,
            user_id=self.scope.user_id, session_id=self.scope.session_id)
        if self.vision_fn is None:
            raise reader.ReaderError('model_unavailable')
        pages = (await self.db.scalars(select(DocumentPage).where(
            DocumentPage.revision_id == revision_id,
            DocumentPage.preview_asset_id == args.asset_id))).all()
        block_id = None
        if not pages:
            rows = (await self.db.execute(select(DocumentPage, DocumentBlock.block_id)
                .join(DocumentBlock, (DocumentBlock.revision_id == DocumentPage.revision_id) &
                                     (DocumentBlock.page_id == DocumentPage.page_id))
                .join(DocumentBlockAsset,
                    (DocumentBlockAsset.revision_id == DocumentBlock.revision_id) &
                    (DocumentBlockAsset.block_id == DocumentBlock.block_id))
                .where(DocumentPage.revision_id == revision_id,
                       DocumentBlockAsset.asset_id == args.asset_id))).all()
            if rows and len(rows) != 1:
                raise reader.ReaderError('not_found')
            pages = [p for p, _ in rows]
            if len(rows) == 1:
                block_id = rows[0][1]
        if asset.kind == 'pdf_preview':
            if args.page_id is None:
                raise reader.ReaderError('invalid_arguments')
            page = await self.db.get(DocumentPage, (revision_id, args.page_id))
            pages = [page] if page is not None else []
            block_id = None
        elif args.page_id is not None:
            pages = [p for p in pages if p.page_id == args.page_id]
        if len({p.page_id for p in pages}) != 1 or not pages[0].preview_page_no:
            raise reader.ReaderError('not_found')
        page = pages[0]
        try:
            path = document_storage.verified_path(
                self.storage_root or settings.DOCUMENTS_STORAGE_ROOT,
                asset.storage_key, asset.sha256)
            # Rendering takes up to a few hundred ms; keep it off the event loop.
            image, width, height = await asyncio.to_thread(
                _render_image, path, asset.kind, page.preview_page_no)
            if len(image) > 3_000_000:
                raise ValueError('image_too_large')
        except Exception as exc:
            raise reader.ReaderError('image_unavailable', type(exc).__name__) from None
        try:
            # The vision client has its own time limit (the run's call time).
            reply = await self.vision_fn(image, 'image/png', args.question, width, height)
            await self._revision(source)
            return reader.envelope([{
                'document_id': str(source), 'revision_id': str(revision_id),
                'page_id': str(page.page_id), 'pdf_page_no': page.preview_page_no,
                'block_id': str(block_id) if block_id else None,
                'asset_id': str(asset.id), 'origin': 'live_visual',
                'content': reply.observation,
                'uncertainties': list(reply.uncertainties),
                'model': reply.model,
            }])
        except (ResearchQuotaExceeded, ResearchRunStopped, DocumentNotFound):
            raise
        except VisionError as exc:
            # The model is told what to change; the detail stays in the trace.
            raise reader.ReaderError(exc.code, VISION_HINTS.get(exc.code, exc.detail)) from None
        except Exception as exc:
            raise reader.ReaderError('vision_unreachable', type(exc).__name__) from None


VISION_HINTS = {
    'vision_timeout': 'the vision model took too long; ask a narrower question about one part of the image',
    'vision_bad_output': 'the vision reply was unusable; ask a shorter, narrower question',
}


def _render_image(path, kind: str, page_no: int) -> tuple[bytes, int, int]:
    """PNG of the selected physical PDF page, or of a PNG/JPEG preview downscaled.
    The file path is never exposed to the model."""
    import pymupdf
    with pymupdf.open(path) as image_doc:
        target = image_doc[page_no - 1] if kind == 'pdf_preview' else image_doc[0]
        factor = min(1.0, 1400 / max(target.rect.width, target.rect.height))
        if kind == 'pdf_preview':
            factor = min(1.5, 1400 / max(target.rect.width, target.rect.height))
        pix = target.get_pixmap(matrix=pymupdf.Matrix(factor, factor), alpha=False)
        return pix.tobytes('png'), pix.width, pix.height


class PerCallTools:
    """ResearchTools with a new database session for each call.

    A research run lasts up to a minute and waits for models between tool
    calls; it does not keep one database session open all that time.
    """

    def __init__(self, scope: ResearchScope, session_factory, **options):
        # options: embed_fn, rerank_fn, vision_fn, storage_root (see ResearchTools).
        self.scope, self.session_factory, self.options = scope, session_factory, options

    def _tools(self, db) -> ResearchTools:
        return ResearchTools(db, self.scope, **self.options)

    async def execute(self, name: str, arguments: dict) -> dict:
        async with self.session_factory() as db:
            return await self._tools(db).execute(name, arguments)

    async def page_index(self) -> dict:
        async with self.session_factory() as db:
            return await self._tools(db).page_index()

    async def _revision(self, document_id: uuid.UUID):
        async with self.session_factory() as db:
            return await self._tools(db)._revision(document_id)

    async def _check_session(self):
        async with self.session_factory() as db:
            return await self._tools(db)._check_session()
