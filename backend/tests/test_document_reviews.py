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
