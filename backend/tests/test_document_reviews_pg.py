"""Real PostgreSQL checks; only run against the isolated worker test database."""
import asyncio
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from backend.app.models import Document, DocumentReview, DocChunk
from backend.app.services.document_reviews import register_reviews, resolve_review, ReviewConflict
from backend.tests.test_document_reviews import item
from backend.tests.test_ingest import _seed_document
from backend.tests.test_integration_rag import _fixed_embed


@pytest.mark.asyncio
async def test_review_migration_creates_table_and_is_repeatable(pg_session):
    await pg_session.execute(text('DROP TABLE document_reviews'))
    root=Path(__file__).resolve().parents[2]
    for _ in range(2):
        for filename in ('worker_page_sources.sql','worker_document_reviews.sql'):
            for statement in (root/'scripts'/filename).read_text().split(';'):
                if statement.strip():await pg_session.execute(text(statement))
        await pg_session.commit()
    columns=(await pg_session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='document_reviews'"))).scalars().all()
    assert {'revision','source_ref','resolved_by','candidate_content'}<=set(columns)


@pytest.mark.asyncio
async def test_concurrent_confirmation_publishes_only_once(pg_session,tmp_path):
    doc=await _seed_document(pg_session,tmp_path)
    row=(await register_reviews(pg_session,doc,[item(doc,ids=[])]))[0]
    await pg_session.commit()
    doc_id,review_id,teacher_id=doc.id,row.id,doc.uploaded_by
    factory=async_sessionmaker(pg_session.bind,expire_on_commit=False)
    async def submit(content):
        async with factory() as db:
            current=await db.get(Document,doc_id)
            try:
                await resolve_review(db,current,review_id,revision=1,status='confirmed',content=content,
                                     teacher_id=teacher_id,embed_fn=_fixed_embed)
                return 'saved'
            except ReviewConflict:
                await db.rollback()
                return 'conflict'
    results=await asyncio.wait_for(asyncio.gather(submit('first verified description'),submit('second verified description')),20)
    assert sorted(results)==['conflict','saved']
    await pg_session.refresh(row)
    chunks=list((await pg_session.scalars(select(DocChunk).where(DocChunk.document_id==doc_id))).all())
    assert row.revision==2 and row.status=='confirmed'
    assert len(chunks)==1 and chunks[0].content==row.final_content


@pytest.mark.asyncio
async def test_partial_review_content_search_is_course_scoped(pg_session,tmp_path):
    from backend.worker.partial_ingest import ingest_pages
    from backend.tests.test_worker_partial import result
    from backend.app.services.retrieval_service import bm25_search
    doc=await _seed_document(pg_session,tmp_path)
    other=await _seed_document(pg_session,tmp_path)
    report={'version':'region-reader-test','source_sha256':doc.content_hash,'page_count':1,
            'page_results':[{'page_no':1,'status':'partial'}],
            'reviews':[item(doc,ids=[],candidate_content='unverifiedgiraffe')]}
    await ingest_pages(pg_session,doc,[result(1,'allocation memoire')],report,
                       embed_fn=_fixed_embed,context_fn=None,token_budget=None)
    assert doc.status.value=='partially_indexed'
    assert await bm25_search(pg_session,'allocation',doc.lab_id,class_id=doc.class_id)
    assert not await bm25_search(pg_session,'unverifiedgiraffe',doc.lab_id,class_id=doc.class_id)
    assert not await bm25_search(pg_session,'allocation',other.lab_id,class_id=other.class_id)


@pytest.mark.asyncio
async def test_concurrent_range_confirmation_embeds_outside_lock_and_publishes_once(pg_session,tmp_path):
    from backend.tests.test_document_reviews import indexed_visual
    from backend.tests.test_worker_source_integrity import CODE
    doc,_,row=await indexed_visual(pg_session,tmp_path,CODE,embed=_fixed_embed)
    doc_id,review_id,teacher_id=doc.id,row.id,doc.uploaded_by
    factory=async_sessionmaker(pg_session.bind,expire_on_commit=False)
    async def submit(content):
        async with factory() as db:
            current=await db.get(Document,doc_id)
            try:
                await resolve_review(db,current,review_id,revision=1,status='confirmed',content=content,
                                     teacher_id=teacher_id,embed_fn=_fixed_embed)
                return 'saved'
            except ReviewConflict:
                await db.rollback()
                return 'conflict'
    results=await asyncio.wait_for(asyncio.gather(submit('first explanation'),submit('second explanation')),20)
    assert sorted(results)==['conflict','saved']
    await pg_session.refresh(row)
    code=[c for c in (await pg_session.scalars(select(DocChunk).where(DocChunk.document_id==doc_id))).all()
          if 'return 1;' in c.content]
    assert len(code)==1 and code[0].content==row.final_content+'\nif (x) {\n    return 1;\n}'
