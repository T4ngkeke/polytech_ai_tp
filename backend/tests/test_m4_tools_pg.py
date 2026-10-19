"""M4-A tools against isolated pgvector and a PDF uploaded through product APIs."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.main import app
from backend.app.models import (
    ClassStudent, DocChunk, Document, DocumentAsset, DocumentBlock, DocumentPage,
    DocumentRevision, EMBEDDING_DIM, Session,
)
from backend.app.routers.teacher import get_storage_root
from backend.app.agent.tools import ResearchTools, create_scope
from backend.app.services import document_edits
from backend.app.services.document_access import DocumentNotFound
from backend.tests.conftest import make_user
from backend.tests.test_structured_index_m3b import _parsed_pdf
from backend.worker.structured_index import process_index_job


async def _indexed_pdf(db, tmp_path, monkeypatch, *, language='fr'):
    doc, rev, teacher, _ = await _parsed_pdf(db, tmp_path, monkeypatch)
    doc_id = doc.id
    if language == 'en':
        # The language is found from the text: make the whole document English.
        blocks = (await db.scalars(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev.id, DocumentBlock.content != ''))).all()
        for block in blocks:
            block.content = 'Recursive functions are explained in this chapter.'
        await db.commit()

    async def embed(texts):
        return [[0.0] * (EMBEDDING_DIM - 1) + [1.0] for _ in texts]

    assert await process_index_job(db, embed_fn=embed)   # every page read: published
    doc = await db.get(Document, doc_id, populate_existing=True)
    await db.refresh(teacher)
    await db.refresh(rev)
    assert doc.current_revision_id == rev.id and doc.language == language
    student = make_user()
    outsider = make_user()
    db.add_all([student, outsider])
    await db.flush()
    db.add(ClassStudent(class_id=doc.class_id, student_id=student.id))
    chat = Session(id=uuid.uuid4(), lab_id=doc.lab_id, user_id=student.id)
    outside_chat = Session(id=uuid.uuid4(), lab_id=doc.lab_id, user_id=outsider.id)
    db.add_all([chat, outside_chat])
    await db.commit()
    return doc, rev, teacher, student, outsider, chat, outside_chat, embed


@pytest.mark.asyncio
async def test_scoped_pdf_search_read_cursor_and_asset_contract(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, rev, _, student, outsider, chat, outside_chat, embed = await _indexed_pdf(
            db, tmp_path, monkeypatch)
        scope = await create_scope(db, user_id=student.id, session_id=chat.id)
        assert scope.snapshot_revisions == {doc.id: rev.id}
        with pytest.raises(DocumentNotFound):
            await create_scope(db, user_id=outsider.id, session_id=outside_chat.id)
        tools = ResearchTools(db, scope)
        result = await tools.execute('search', {'query': 'Définition θ₁', 'kind': 'text'})
        assert result['ok'] and result['items'] and 'embedding_unavailable' in result['issues']
        item = next(x for x in result['items'] if 'Définition θ₁' in x['snippets'][0]['content'])
        assert item['document_id'] == str(doc.id) and item['revision_id'] == str(rev.id)
        assert item['pdf_page_no'] == 1 and item['page_id']
        assert len(item['snippets']) > 1  # block-internal chunks are grouped
        block = await db.scalar(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev.id,
            DocumentBlock.block_id == uuid.UUID(item['block_id'])))
        for snippet in item['snippets']:
            r = snippet['range']
            assert block.content[r['start']:r['end']] == snippet['content']
        code_only = await tools.execute('search', {'query': 'Définition θ₁', 'kind': 'code'})
        assert code_only['ok'] and not any(
            x['block_id'] == item['block_id'] for x in code_only['items'])
        assert (await tools.execute('search', {'query': 'Définition', 'document_id': str(uuid.uuid4())}))[
            'error']['code'] == 'not_found'
        assert (await tools.execute('search', {'query': 'Définition', 'top_k': 13}))[
            'error']['code'] == 'invalid_arguments'
        assert (await tools.execute('search', {'query': 'Définition', 'lab_id': str(doc.lab_id)}))[
            'error']['code'] == 'invalid_arguments'

        parts, cursor = [], None
        while True:
            response = await tools.execute('read', {
                'document_id': str(doc.id), 'block_id': item['block_id'],
                'budget': 31, **({'cursor': cursor} if cursor else {}),
            })
            assert response['ok'] and response['items']
            parts.append(response['items'][0])
            cursor = response['next_cursor']
            if cursor is None:
                break
        assert ''.join(p['content'] for p in parts) == block.content
        assert all(a['range']['end'] == b['range']['start'] for a, b in zip(parts, parts[1:]))
        another = ResearchTools(db, await create_scope(db, user_id=student.id, session_id=chat.id))
        assert (await another.execute('read', {
            'document_id': str(doc.id), 'block_id': item['block_id'],
            'budget': 31, 'cursor': (await tools.execute('read', {
                'document_id': str(doc.id), 'block_id': item['block_id'], 'budget': 31,
            }))['next_cursor'],
        }))['error']['code'] == 'invalid_cursor'
        opened = await tools.execute('open', {'document_id': str(doc.id)})
        assert opened['ok'] and opened['items'][0]['preview_page_no'] == 1
        grep = await tools.execute('grep', {'document_id': str(doc.id), 'pattern': 'Définition θ₁'})
        assert grep['ok'] and grep['items']
        page = await db.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id))
        index = await tools.page_index()
        assert list(index) == [str(doc.id)] and index[str(doc.id)]['filename'] == doc.filename
        assert index[str(doc.id)]['pages'][0] == {'page_id': str(page.page_id), 'pdf_page_no': 1}
        assert await ResearchTools(db, await create_scope(
            db, user_id=student.id, session_id=chat.id)).page_index() == index
        assert (await tools.execute('navigate', {
            'document_id': str(doc.id), 'anchor_block_id': item['block_id'],
            'direction': 'next', 'unit': 'page',
        }))['ok']
        # A visible page asset passes access checks but vision is not connected in M4-A.
        asset = await db.get(DocumentAsset, page.preview_asset_id)
        assert asset is not None
        assert (await tools.execute('view_image', {
            'asset_id': str(asset.id), 'question': 'Que voit-on ?',
        }))['error']['code'] == 'model_unavailable'
        assert (await tools.execute('view_image', {
            'asset_id': str(uuid.uuid4()), 'question': 'Que voit-on ?',
        }))['error']['code'] == 'not_found'
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_snapshot_survives_republication_but_membership_revokes_it(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, old, teacher, student, _, chat, _, embed = await _indexed_pdf(
            db, tmp_path, monkeypatch)
        old_scope = await create_scope(db, user_id=student.id, session_id=chat.id)
        text = await db.scalar(select(DocumentBlock).where(
            DocumentBlock.revision_id == old.id,
            DocumentBlock.kind == 'text'))
        doc = await db.get(Document, doc.id, populate_existing=True)
        edited = await document_edits.edit_block(
            db, document_id=doc.id, teacher_id=teacher.id,
            block_id=text.block_id, expected_revision_id=old.id,
            expected_generation=doc.generation, content='Nouveau chapitre uniquement',
        )
        await db.commit()
        await db.refresh(doc)
        prepublication = await create_scope(db, user_id=student.id, session_id=chat.id)
        assert prepublication.snapshot_revisions[doc.id] == old.id
        assert await process_index_job(db, embed_fn=embed)
        # The teacher's edit is published as soon as it is indexed.
        doc = await db.get(Document, doc.id, populate_existing=True)
        await db.refresh(edited)
        assert doc.current_revision_id == edited.id
        old_tools = ResearchTools(db, old_scope)
        old_result = await old_tools.execute('search', {'query': 'Définition θ₁'})
        assert old_result['ok'] and old_result['items']
        assert {x['revision_id'] for x in old_result['items']} == {str(old.id)}
        new_scope = await create_scope(db, user_id=student.id, session_id=chat.id)
        assert new_scope.snapshot_revisions[doc.id] == edited.id
        new_result = await ResearchTools(db, new_scope).execute('search', {
            'query': 'Nouveau chapitre'})
        assert new_result['ok'] and any(x['revision_id'] == str(edited.id) for x in new_result['items'])
        # Revoke from another connection while the research session stays open.
        async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
            await other.delete(await other.get(ClassStudent, (doc.class_id, student.id)))
            await other.commit()
        denied = await old_tools.execute('read', {
            'document_id': str(doc.id),
            'block_id': old_result['items'][0]['block_id'],
        })
        assert denied['error']['code'] == 'not_found'
        assert (await old_tools.execute('search', {'query': 'Définition'}))[
            'error']['code'] == 'not_found'
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_english_pdf_search_uses_its_index_language(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, rev, _, student, _, chat, _, _ = await _indexed_pdf(
            db, tmp_path, monkeypatch, language='en')
        tools = ResearchTools(db, await create_scope(
            db, user_id=student.id, session_id=chat.id))
        result = await tools.execute('search', {
            'query': 'recursive function', 'document_id': str(doc.id), 'kind': 'text',
        })
        assert result['ok'] and any(
            item['revision_id'] == str(rev.id) and
            any('functions' in s['content'] for s in item['snippets'])
            for item in result['items'])
    finally:
        app.dependency_overrides.pop(get_storage_root, None)

@pytest.mark.asyncio
async def test_live_visual_page_is_scoped_and_failures_are_not_evidence(pg_session, tmp_path, monkeypatch):
    from backend.app.services.research_vision import VisualReply
    from backend.app.agent.evidence import EvidenceStore
    try:
        db = pg_session
        doc, rev, _, student, outsider, chat, outside_chat, _ = await _indexed_pdf(
            db, tmp_path, monkeypatch)
        scope = await create_scope(db, user_id=student.id, session_id=chat.id)
        page = await db.scalar(select(DocumentPage).where(
            DocumentPage.revision_id == rev.id,
            DocumentPage.preview_asset_id.is_not(None)).limit(1))
        seen = []
        async def vision(image, mime, question, width, height):
            seen.append((mime, question, width, height, len(image)))
            return VisualReply('A labelled diagram is visible.',
                               ('Arrow direction is unclear.',), 'fake-vlm', 50, 12)
        tools = ResearchTools(db, scope, vision_fn=vision,
                              storage_root=tmp_path / 'storage')
        output = await tools.execute('view_image', {
            'asset_id': str(page.preview_asset_id), 'question': 'What is shown?'})
        assert output['ok'] and output['items'][0]['origin'] == 'live_visual'
        assert seen and seen[0][0] == 'image/png' and seen[0][2] <= 1400
        evidence = EvidenceStore(scope.snapshot_revisions)
        ids = evidence.add_visual(output)
        assert ids == ['E1'] and evidence.get('E1').citation()['range'] is None
        assert evidence.get('E1').citation()['asset_id'] == str(page.preview_asset_id)

        from backend.app.services.research_vision import VisionError
        async def timeout(*_args):
            raise VisionError('vision_timeout', 'no reply within 20 s')
        failed = ResearchTools(db, scope, vision_fn=timeout,
                               storage_root=tmp_path / 'storage')
        bad = await failed.execute('view_image', {
            'asset_id': str(page.preview_asset_id), 'question': 'What is shown?'})
        # The model learns what to change, not just that the model is unavailable.
        assert not bad['ok'] and bad['error']['code'] == 'vision_timeout'
        assert 'narrower question' in bad['error']['message']
        assert evidence.add_visual(bad) == []
        async def broken(*_args):
            raise RuntimeError('socket')
        other = await ResearchTools(db, scope, vision_fn=broken, storage_root=tmp_path / 'storage'
            ).execute('view_image', {'asset_id': str(page.preview_asset_id), 'question': 'What?'})
        assert other['error']['code'] == 'vision_unreachable'
        # No retry: a failed call does not use the visual budget, only time.
        assert 'do not call view_image again' in other['error']['message']
        assert 'socket' not in other['error']['message']
        # A missing image file is not reported as a model problem.
        missing = await ResearchTools(db, scope, vision_fn=vision, storage_root=tmp_path / 'elsewhere'
            ).execute('view_image', {'asset_id': str(page.preview_asset_id), 'question': 'What?'})
        assert missing['error']['code'] == 'image_unavailable'
        assert (await tools.execute('view_image', {
            'asset_id': str(uuid.uuid4()), 'question': 'What is shown?'}))['error']['code'] == 'not_found'
    finally:
        app.dependency_overrides.pop(get_storage_root, None)
