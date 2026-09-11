"""Review records are independent of mutable ingestion reports."""
import pytest
from sqlalchemy import select
from backend.tests.test_ingest import _seed_document
from backend.app.services.document_reviews import register_reviews, ReviewConflict
from backend.app.models import DocumentReview


def item(doc, **kwargs):
    return {'file_sha256':doc.content_hash,'page_no':1,'ids':['p1l0'],
            'region_ids':['p1r0'],'bbox':[10,10,100,100],'reason':'Arrow direction unclear',**kwargs}


@pytest.mark.asyncio
async def test_register_deduplicates_by_source_not_crop_or_reason(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path)
    await register_reviews(db_session,doc,[item(doc)])
    await db_session.commit()
    await register_reviews(db_session,doc,[item(doc,bbox=[0,0,120,120],reason='Please inspect connections')])
    await db_session.commit()
    rows=list((await db_session.scalars(select(DocumentReview))).all())
    assert len(rows)==1
    assert rows[0].reason=='Please inspect connections'
    assert rows[0].revision==2


@pytest.mark.asyncio
async def test_rerun_does_not_reopen_teacher_resolution(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path)
    rows=await register_reviews(db_session,doc,[item(doc)])
    rows[0].status='ignored'
    await db_session.commit()
    await register_reviews(db_session,doc,[item(doc,reason='New model wording')])
    assert rows[0].status=='ignored'
    assert rows[0].reason=='Arrow direction unclear'


@pytest.mark.asyncio
async def test_file_change_rejected_before_creating_review(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path)
    with pytest.raises(ReviewConflict):
        await register_reviews(db_session,doc,[item(doc,file_sha256='other')])
    assert not list((await db_session.scalars(select(DocumentReview))).all())


@pytest.mark.asyncio
async def test_invalid_review_never_partially_registers_batch(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path)
    with pytest.raises(ValueError):
        await register_reviews(db_session,doc,[item(doc),item(doc,bbox=[100,0,10,10])])
    assert not list((await db_session.scalars(select(DocumentReview))).all())


@pytest.mark.asyncio
async def test_document_deletion_removes_review_records(db_session,tmp_path):
    from backend.app.services.document_service import delete_document
    doc=await _seed_document(db_session,tmp_path)
    await register_reviews(db_session,doc,[item(doc)])
    await db_session.commit()
    await delete_document(db_session,doc)
    await db_session.commit()
    assert not list((await db_session.scalars(select(DocumentReview))).all())


@pytest.mark.asyncio
async def test_stale_review_cannot_change_state(db_session,tmp_path):
    from backend.app.services.document_reviews import resolve_review
    doc=await _seed_document(db_session,tmp_path)
    row=(await register_reviews(db_session,doc,[item(doc)]))[0]
    await db_session.commit()
    with pytest.raises(ReviewConflict):
        await resolve_review(db_session,doc,row.id,revision=0,status='ignored',content=None,
                             teacher_id=doc.uploaded_by,embed_fn=None)
    assert row.status=='pending'


@pytest.mark.asyncio
async def test_ignored_review_preserves_native_index(db_session,tmp_path):
    from backend.app.services.document_reviews import resolve_review
    from backend.tests.test_ingest import _chunks_of, fake_embed
    from backend.worker.ingest import ingest_document
    doc=await _seed_document(db_session,tmp_path)
    await ingest_document(db_session,doc.id,embed_fn=fake_embed)
    original=[(c.id,c.content) for c in await _chunks_of(db_session,doc)]
    row=(await register_reviews(db_session,doc,[item(doc)]))[0]
    await db_session.commit()
    await resolve_review(db_session,doc,row.id,revision=1,status='ignored',content=None,
                         teacher_id=doc.uploaded_by,embed_fn=None)
    assert [(c.id,c.content) for c in await _chunks_of(db_session,doc)]==original
    assert row.status=='ignored' and row.revision==2


@pytest.mark.asyncio
async def test_failed_embedding_keeps_review_pending(db_session,tmp_path):
    from backend.app.services.document_reviews import resolve_review
    doc=await _seed_document(db_session,tmp_path)
    row=(await register_reviews(db_session,doc,[item(doc,ids=[])]))[0]
    await db_session.commit()
    async def broken(texts):raise RuntimeError('embedding unavailable')
    with pytest.raises(RuntimeError):
        await resolve_review(db_session,doc,row.id,revision=1,status='confirmed',content='Confirmed explanation',
                             teacher_id=doc.uploaded_by,embed_fn=broken)
    assert row.status=='pending' and row.revision==1


@pytest.mark.asyncio
async def test_confirmation_indexes_reviewed_text_with_course_scope(db_session,tmp_path):
    from backend.app.services.document_reviews import resolve_review
    from backend.tests.test_ingest import _chunks_of,fake_embed
    doc=await _seed_document(db_session,tmp_path)
    row=(await register_reviews(db_session,doc,[item(doc,ids=[])]))[0]
    await db_session.commit()
    await resolve_review(db_session,doc,row.id,revision=1,status='confirmed',content='Confirmed explanation',
                         teacher_id=doc.uploaded_by,embed_fn=fake_embed)
    chunks=await _chunks_of(db_session,doc)
    assert len(chunks)==1 and chunks[0].content=='Confirmed explanation'
    assert chunks[0].class_id==doc.class_id and chunks[0].lab_id==doc.lab_id
    assert chunks[0].edited_by_teacher
    assert row.status=='confirmed'


@pytest.mark.asyncio
async def test_review_routes_are_owned_and_revision_checked(db_session,tmp_path):
    from backend.app.main import app
    from backend.app.models import User,UserRole
    from backend.tests.conftest import make_client,make_user
    doc=await _seed_document(db_session,tmp_path)
    row=(await register_reviews(db_session,doc,[item(doc)]))[0]
    other=make_user(role=UserRole.teacher);db_session.add(other);await db_session.commit()
    teacher=await db_session.get(User,doc.uploaded_by)
    path=f'/api/teacher/documents/{doc.id}/reviews'
    client=await make_client(db_session,other)
    try:
        assert (await client.get(path)).status_code==404
    finally:await client.aclose();app.dependency_overrides.clear()
    client=await make_client(db_session,teacher)
    try:
        response=await client.get(path)
        assert response.status_code==200 and len(response.json())==1
        response=await client.put(f'{path}/{row.id}',json={'revision':2,'status':'ignored'})
        assert response.status_code==409
        response=await client.put(f'{path}/{row.id}',json={'revision':1,'status':'ignored'})
        assert response.status_code==200 and response.json()['status']=='ignored'
    finally:await client.aclose();app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_review_image_is_tied_to_original_bytes(db_session,tmp_path):
    import hashlib,pymupdf
    from backend.app.services.document_reviews import render_review_image
    doc=await _seed_document(db_session,tmp_path)
    path=tmp_path/'review.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((20,40),'example');pdf.save(path)
    doc.storage_path=str(path);doc.content_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    row=(await register_reviews(db_session,doc,[item(doc)]))[0]
    assert render_review_image(doc,row).startswith(b'\x89PNG')
    path.write_bytes(b'changed source')
    with pytest.raises(ReviewConflict):render_review_image(doc,row)


@pytest.mark.asyncio
async def test_page_partial_ingest_registers_reviews_without_indexing_candidate(db_session,tmp_path):
    from backend.worker.partial_ingest import ingest_pages
    from backend.tests.test_worker_partial import result
    from backend.tests.test_ingest import fake_embed,_chunks_of
    doc=await _seed_document(db_session,tmp_path)
    report={'version':'region-reader-test','source_sha256':doc.content_hash,'page_count':1,
            'page_results':[{'page_no':1,'status':'partial'}],
            'reviews':[item(doc,ids=[],candidate_content='Unverified guess')]}
    await ingest_pages(db_session,doc,[result(1,'Native code stays')],report,
                       embed_fn=fake_embed,context_fn=None,token_budget=None)
    assert doc.status.value=='partially_indexed'
    assert [c.content for c in await _chunks_of(db_session,doc)]==['Native code stays']
    rows=list((await db_session.scalars(select(DocumentReview))).all())
    assert len(rows)==1 and rows[0].candidate_content=='Unverified guess'
    assert doc.ingest_report['review_summary']['pending']==1
    await ingest_pages(db_session,doc,[result(1,'Native code stays')],report,
                       embed_fn=fake_embed,context_fn=None,token_budget=None)
    assert len(list((await db_session.scalars(select(DocumentReview))).all()))==1


@pytest.mark.asyncio
async def test_review_overlap_retains_previous_source_instead_of_new_draft(db_session,tmp_path):
    from backend.worker.partial_ingest import ingest_pages
    from backend.tests.test_worker_partial import result
    from backend.tests.test_ingest import fake_embed,_chunks_of
    doc=await _seed_document(db_session,tmp_path)
    report={'source_sha256':doc.content_hash,'page_count':1,'page_results':[{'page_no':1,'status':'read'}]}
    await ingest_pages(db_session,doc,[result(1,'Previous reliable formula')],report,
                       embed_fn=fake_embed,context_fn=None,token_budget=None)
    old=(await _chunks_of(db_session,doc))[0]
    old_id=old.id
    report={**report,'version':'region-reader-test','reviews':[item(doc,region_ids=[])]}
    await ingest_pages(db_session,doc,[result(1,'Unclear extracted formula')],report,
                       embed_fn=fake_embed,context_fn=None,token_budget=None)
    chunks=await _chunks_of(db_session,doc)
    assert [(c.id,c.content) for c in chunks]==[(old_id,'Previous reliable formula')]
    assert doc.status.value=='partially_indexed'


@pytest.mark.asyncio
async def test_review_resolution_during_embedding_keeps_page_snapshot(db_session,tmp_path):
    from backend.worker.partial_ingest import ingest_pages
    from backend.tests.test_worker_partial import result
    from backend.tests.test_ingest import fake_embed,_chunks_of
    doc=await _seed_document(db_session,tmp_path)
    report={'version':'region-reader-test','source_sha256':doc.content_hash,'page_count':1,
            'page_results':[{'page_no':1,'status':'partial'}],'reviews':[item(doc,ids=[])]}
    await ingest_pages(db_session,doc,[result(1,'Previous text')],report,
                       embed_fn=fake_embed,context_fn=None,token_budget=None)
    row=(await db_session.scalars(select(DocumentReview))).one()
    async def edit_during_embedding(texts):
        row.status='ignored';row.revision+=1
        await db_session.commit()
        return await fake_embed(texts)
    await ingest_pages(db_session,doc,[result(1,'New worker text')],report,
                       embed_fn=edit_during_embedding,context_fn=None,token_budget=None)
    assert [c.content for c in await _chunks_of(db_session,doc)]==['Previous text']
    assert row.status=='ignored'
    assert 'during processing' in doc.ingest_report['page_reader']['page_results'][0]['error']


@pytest.mark.asyncio
async def test_ignored_visual_not_reintroduced_on_rerun(db_session,tmp_path):
    from dataclasses import replace
    from backend.worker.partial_ingest import ingest_pages
    from backend.worker.page_reader import ReadGroup
    from backend.tests.test_worker_partial import result
    from backend.tests.test_ingest import fake_embed,_chunks_of
    doc=await _seed_document(db_session,tmp_path)
    row=(await register_reviews(db_session,doc,[item(doc,region_ids=[])]))[0]
    row.status='ignored';await db_session.commit()
    page=result(1,'23')
    page=replace(page,groups=(ReadGroup('formula',('p1l0',),'Unwanted visual interpretation'),))
    report={'version':'region-reader-test','source_sha256':doc.content_hash,'page_count':1,
            'page_results':[{'page_no':1,'status':'read'}],'reviews':[]}
    await ingest_pages(db_session,doc,[page],report,embed_fn=fake_embed,context_fn=None,token_budget=None)
    assert [c.content for c in await _chunks_of(db_session,doc)]==['23']
    assert row.status=='ignored'


@pytest.mark.asyncio
async def test_confirmation_commit_failure_rolls_back_index_and_state(db_session,tmp_path,monkeypatch):
    from backend.app.services.document_reviews import resolve_review
    from backend.tests.test_ingest import fake_embed
    from backend.app.models import DocChunk
    doc=await _seed_document(db_session,tmp_path)
    row=(await register_reviews(db_session,doc,[item(doc,ids=[])]))[0]
    await db_session.commit()
    review_id=row.id
    async def broken_commit():raise RuntimeError('database commit failed')
    monkeypatch.setattr(db_session,'commit',broken_commit)
    with pytest.raises(RuntimeError,match='database commit failed'):
        try:
            await resolve_review(db_session,doc,review_id,revision=1,status='confirmed',
                content='Verified diagram',teacher_id=doc.uploaded_by,embed_fn=fake_embed)
        except Exception:
            # Same transaction cleanup as get_db on a failing request.
            await db_session.rollback()
            raise
    current=await db_session.get(DocumentReview,review_id)
    assert current.status=='pending' and current.revision==1
    assert not list((await db_session.scalars(select(DocChunk))).all())


@pytest.mark.asyncio
async def test_existing_review_blocks_same_document_file_version_change(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path)
    await register_reviews(db_session,doc,[item(doc)])
    await db_session.commit()
    doc.content_hash='b'*64
    with pytest.raises(ReviewConflict,match='Source file changed'):
        await register_reviews(db_session,doc,[item(doc)])


# --- v3 confirmation: prepare outside the lock, publish once, keep native text -

from backend.tests.test_ingest import _chunks_of, fake_embed
from backend.tests.test_worker_partial import (
    visual_document, v15, native_range, review_item, ingest_v15, owner, figure,
)
from backend.tests.test_worker_source_integrity import CODE, PICTURE


async def indexed_visual(session, tmp_path, target, embed=fake_embed):
    doc, (p1, p2) = await visual_document(session, tmp_path)
    groups = [native_range(p1, target)] if target['ids'] and target.get('scope') != 'page' else []
    await ingest_v15(session, doc, [v15(p1, *groups), v15(p2)], [review_item(doc, p1, target)], embed=embed)
    return doc, (p1, p2), (await session.scalars(select(DocumentReview).where(DocumentReview.document_id == doc.id))).one()


async def confirm(session, doc, row, content, embed=fake_embed, revision=None):
    from backend.app.services.document_reviews import resolve_review
    return await resolve_review(session, doc, row.id, revision=row.revision if revision is None else revision,
                                status='confirmed', content=content, teacher_id=doc.uploaded_by, embed_fn=embed)


@pytest.mark.asyncio
async def test_confirming_code_range_keeps_program_copied_code(db_session, tmp_path):
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    before = owner(await _chunks_of(db_session, doc), 'p1l1').id
    await confirm(db_session, doc, row, 'The code returns 1 when x holds.')
    chunks = await _chunks_of(db_session, doc)
    code = [c for c in chunks if 'return 1;' in c.content]
    assert len(code) == 1
    assert code[0].content == 'The code returns 1 when x holds.\nif (x) {\n    return 1;\n}'
    assert code[0].id == before and code[0].edited_by_teacher
    assert row.status == 'confirmed' and row.final_content == 'The code returns 1 when x holds.'


@pytest.mark.asyncio
async def test_long_confirmation_keeps_first_and_last_part(db_session, tmp_path):
    doc, _, row = await indexed_visual(db_session, tmp_path, PICTURE)
    text = ''.join(f'Sentence {i} about the linked list.\n' for i in range(150)).strip()
    await confirm(db_session, doc, row, text)
    parts = sorted([c for c in await _chunks_of(db_session, doc) if 'p1l4' in c.source_ref['ids']],
                   key=lambda c: c.source_ref['part'])
    assert len(parts) > 2
    assert ''.join(c.content for c in parts) == text + '\nNode Next'
    assert [c.source_ref['part'] for c in parts] == list(range(len(parts)))
    assert {c.source_ref['parts'] for c in parts} == {len(parts)}
    indexes = [c.chunk_index for c in parts]
    assert indexes == list(range(indexes[0], indexes[0] + len(parts)))


@pytest.mark.asyncio
async def test_confirmation_refuses_overlapping_chunk(db_session, tmp_path):
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    await ingest_v15(db_session, doc, [v15(p1), v15(p2)])
    row = (await register_reviews(db_session, doc, [review_item(doc, p1, PICTURE)]))[0]
    await db_session.commit()
    review_id = row.id
    before = {(c.id, c.content) for c in await _chunks_of(db_session, doc)}
    with pytest.raises(ReviewConflict) as caught:
        await confirm(db_session, doc, row, 'Two linked nodes.')
    assert caught.value.code == 'review_source_conflict'
    await db_session.rollback()
    await db_session.refresh(doc)
    assert {(c.id, c.content) for c in await _chunks_of(db_session, doc)} == before
    assert (await db_session.get(DocumentReview, review_id)).status == 'pending'


@pytest.mark.asyncio
async def test_teacher_edit_during_embedding_blocks_stale_confirmation(db_session, tmp_path):
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    review_id = row.id
    heading = owner(await _chunks_of(db_session, doc), 'p1l0')
    async def racing(texts):
        heading.content = 'Teacher heading.'; heading.edited_by_teacher = True
        await db_session.commit()
        return await fake_embed(texts)
    with pytest.raises(ReviewConflict) as caught:
        await confirm(db_session, doc, row, 'Explanation.', embed=racing)
    assert caught.value.code == 'review_revision_conflict'
    await db_session.rollback()
    await db_session.refresh(doc)
    chunks = await _chunks_of(db_session, doc)
    assert owner(chunks, 'p1l1').content == 'if (x) {\n    return 1;\n}'
    assert owner(chunks, 'p1l0').content == 'Teacher heading.'
    current = await db_session.get(DocumentReview, review_id)
    assert (current.status, current.revision) == ('pending', 1)


@pytest.mark.asyncio
async def test_embedding_failure_leaves_range_unchanged(db_session, tmp_path):
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    review_id = row.id
    before = {(c.id, c.content) for c in await _chunks_of(db_session, doc)}
    async def broken(texts): raise RuntimeError('embedding unavailable')
    with pytest.raises(RuntimeError):
        await confirm(db_session, doc, row, 'Explanation.', embed=broken)
    await db_session.rollback()
    await db_session.refresh(doc)
    assert {(c.id, c.content) for c in await _chunks_of(db_session, doc)} == before
    current = await db_session.get(DocumentReview, review_id)
    assert (current.status, current.revision) == ('pending', 1)


@pytest.mark.asyncio
async def test_page_review_confirmation_adds_description_and_keeps_native_blocks(db_session, tmp_path):
    doc, (p1, p2), row = await indexed_visual(db_session, tmp_path,
        {'ids': ['p1l0', 'p1l1', 'p1l2', 'p1l3', 'p1l4', 'p1l5', 'p1l6', 'p1l7'], 'region_ids': ['p1r0'], 'scope': 'page'})
    before = {(c.id, c.content) for c in await _chunks_of(db_session, doc)}
    await confirm(db_session, doc, row, 'The picture shows a linked list.')
    chunks = await _chunks_of(db_session, doc)
    added = [c for c in chunks if (c.id, c.content) not in before]
    assert before <= {(c.id, c.content) for c in chunks}
    assert [c.content for c in added] == ['The picture shows a linked list.']
    assert added[0].source_ref['scope'] == 'page' and added[0].edited_by_teacher
    # A rerun keeps the teacher's page note and still updates the native blocks.
    await ingest_v15(db_session, doc, [v15(p1), v15(p2)])
    chunks = await _chunks_of(db_session, doc)
    assert any(c.id == added[0].id and c.content == 'The picture shows a linked list.' for c in chunks)
    assert 'return 1;' in owner(chunks, 'p1l2').content
    assert doc.ingest_report['page_summary']['updated'] == [1, 2]


@pytest.mark.asyncio
async def test_changed_original_file_blocks_native_confirmation(db_session, tmp_path):
    from pathlib import Path
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    Path(doc.storage_path).write_bytes(b'changed source')
    with pytest.raises(ReviewConflict) as caught:
        await confirm(db_session, doc, row, 'Explanation.')
    assert caught.value.code == 'review_source_changed'


@pytest.mark.asyncio
async def test_stale_revision_has_its_own_error_code(db_session, tmp_path):
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    with pytest.raises(ReviewConflict) as caught:
        await confirm(db_session, doc, row, 'Explanation.', revision=0)
    assert caught.value.code == 'review_revision_conflict'


@pytest.mark.asyncio
async def test_ignoring_v3_range_keeps_native_text(db_session, tmp_path):
    from backend.app.services.document_reviews import resolve_review
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    before = {(c.id, c.content) for c in await _chunks_of(db_session, doc)}
    await resolve_review(db_session, doc, row.id, revision=1, status='ignored', content=None,
                         teacher_id=doc.uploaded_by, embed_fn=None)
    assert {(c.id, c.content) for c in await _chunks_of(db_session, doc)} == before
    assert row.status == 'ignored'


@pytest.mark.asyncio
async def test_v3_review_deduplicates_by_source_not_screenshot_box(db_session, tmp_path):
    doc, (p1, _) = await visual_document(db_session, tmp_path)
    first = review_item(doc, p1, PICTURE)
    await register_reviews(db_session, doc, [first])
    await db_session.commit()
    await register_reviews(db_session, doc, [{**first, 'bbox': [290, 50, 540, 110], 'reason': 'Direction unclear'}])
    rows = (await db_session.scalars(select(DocumentReview))).all()
    assert len(rows) == 1 and rows[0].revision == 2 and rows[0].source_ref['version'] == 3
    with pytest.raises(ReviewConflict) as caught:
        await register_reviews(db_session, doc, [review_item(doc, p1, {'ids': ['p1l4'], 'region_ids': []})])
    assert caught.value.code == 'review_source_conflict'


# --- status and API errors: one state rule, machine-readable conflicts --------

@pytest.mark.asyncio
async def test_resolving_the_last_review_updates_document_state(db_session, tmp_path):
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    assert doc.status.value == 'partially_indexed'
    assert doc.ingest_report['index_state']['pending_review_count'] == 1
    await confirm(db_session, doc, row, 'The code returns 1.')
    assert doc.status.value == 'indexed' and doc.error_message is None
    assert doc.ingest_report['index_state']['pending_review_count'] == 0
    assert doc.ingest_report['page_reader']['page_results'][0]['status'] == 'partial'


@pytest.mark.asyncio
async def test_image_only_page_marked_no_index_is_not_missing(db_session, tmp_path):
    from dataclasses import replace
    from backend.app.services.document_reviews import resolve_review
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    blank = replace(p2, lines=())
    await ingest_v15(db_session, doc, [v15(p1), v15(blank)],
                     [review_item(doc, blank, {'ids': [], 'region_ids': [], 'scope': 'page'},
                                  reason='Scanned page', code='content_review')])
    assert doc.ingest_report['index_state']['missing_pages'] == [2]
    row = (await db_session.scalars(select(DocumentReview))).one()
    await resolve_review(db_session, doc, row.id, revision=row.revision, status='ignored', content=None,
                         teacher_id=doc.uploaded_by, embed_fn=None)
    state = doc.ingest_report['index_state']
    assert state['no_index_pages'] == [2] and state['missing_pages'] == []
    assert doc.status.value == 'indexed'


@pytest.mark.asyncio
async def test_retained_conflict_page_stays_partial_after_reviews_are_resolved(db_session, tmp_path):
    from backend.app.services.document_reviews import resolve_review
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    small = {'ids': ['p1l4'], 'region_ids': []}
    await ingest_v15(db_session, doc, [v15(p1, native_range(p1, small)), v15(p2)], [review_item(doc, p1, small)])
    await ingest_v15(db_session, doc, [v15(p1, native_range(p1, PICTURE)), v15(p2)], [review_item(doc, p1, PICTURE)])
    row = (await db_session.scalars(select(DocumentReview))).one()
    await resolve_review(db_session, doc, row.id, revision=row.revision, status='ignored', content=None,
                         teacher_id=doc.uploaded_by, embed_fn=None)
    assert doc.ingest_report['index_state']['pending_review_count'] == 0
    assert doc.status.value == 'partially_indexed'
    assert doc.ingest_report['index_state']['problem_pages'] == [1]


async def review_client(db_session, user):
    from backend.tests.conftest import make_client
    return await make_client(db_session, user)


@pytest.mark.asyncio
async def test_review_image_route_checks_owner_document_and_source(db_session, tmp_path):
    from pathlib import Path
    from backend.app.main import app
    from backend.app.models import User, UserRole
    from backend.tests.conftest import make_user
    doc, (p1, _), row = await indexed_visual(db_session, tmp_path / 'a', PICTURE)
    other_doc, (q1, _), other_row = await indexed_visual(db_session, tmp_path / 'b', PICTURE)
    stranger = make_user(role=UserRole.teacher); db_session.add(stranger); await db_session.commit()
    teacher = await db_session.get(User, doc.uploaded_by)
    image = f'/api/teacher/documents/{doc.id}/reviews/{row.id}/image'
    client = await review_client(db_session, stranger)
    try:
        assert (await client.get(image)).status_code == 404
    finally:
        await client.aclose(); app.dependency_overrides.clear()
    client = await review_client(db_session, teacher)
    try:
        response = await client.get(image)
        assert response.status_code == 200 and response.content.startswith(b'\x89PNG')
        # A review of another document is never reachable through this document.
        assert (await client.get(f'/api/teacher/documents/{doc.id}/reviews/{other_row.id}/image')).status_code == 404
        row.bbox = [0, 0, 5000, 5000]; await db_session.commit()
        response = await client.get(image)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'review_source_changed'
        row.bbox = [290, 50, 540, 110]; await db_session.commit()
        Path(doc.storage_path).write_bytes(b'changed source')
        response = await client.get(image)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'review_source_changed'
        Path(doc.storage_path).unlink()
        response = await client.get(image)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'review_source_changed'
    finally:
        await client.aclose(); app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_review_update_route_returns_machine_readable_errors(db_session, tmp_path):
    import httpx
    from openai import APIConnectionError
    from backend.app.main import app
    from backend.app.models import User
    from backend.app.routers.teacher import get_embed_fn
    doc, _, row = await indexed_visual(db_session, tmp_path, CODE)
    teacher = await db_session.get(User, doc.uploaded_by)
    path = f'/api/teacher/documents/{doc.id}/reviews/{row.id}'
    async def offline(texts):
        raise APIConnectionError(request=httpx.Request('POST', 'http://embeddings.test/v1'))
    app.dependency_overrides[get_embed_fn] = lambda: offline
    client = await review_client(db_session, teacher)
    try:
        response = await client.put(path, json={'revision': 2, 'status': 'ignored'})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'review_revision_conflict'
        assert response.json()['detail']['message']
        response = await client.put(path, json={'revision': 1, 'status': 'confirmed', 'content': 'Explanation.'})
        assert response.status_code == 503
        assert response.json()['detail']['code'] == 'embedding_unavailable'
        await db_session.refresh(row)
        assert (row.status, row.revision) == ('pending', 1)
        app.dependency_overrides[get_embed_fn] = lambda: fake_embed
        response = await client.put(path, json={'revision': 1, 'status': 'confirmed', 'content': 'Explanation.'})
        assert response.status_code == 200 and response.json()['status'] == 'confirmed'
    finally:
        await client.aclose(); app.dependency_overrides.clear()
