"""Page isolation and source identity contracts, before visual transcription."""
from dataclasses import replace
import pytest
from backend.tests.test_ingest import _seed_document, _chunks_of, fake_embed
from backend.worker.ingest import ingest_document, _source_fingerprint
from backend.worker.chunking import Chunk
from backend.worker.page_reader import NativePage, NativeLine, ReadGroup, ReadPage


def result(number, text='source'):
    return ReadPage(NativePage(number, 600, 400, (NativeLine(f'p{number}l0', (10,10,100,20), text),)),
                    (ReadGroup('text', (f'p{number}l0',), text),), 1)


def reader_for(pages, failed=(), digest='abc'):
    async def reader(path):
        return pages, {'source_sha256': digest, 'page_count': 3, 'page_results': [
            {'page_no': n, 'status': 'needs_review' if n in failed else 'read', 'error': 'unreadable' if n in failed else None}
            for n in range(1,4)]}
    return reader


@pytest.mark.asyncio
async def test_partial_keeps_physical_pages_and_old_failed_page(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)
    doc.storage_path += '.pdf'
    await db_session.commit()
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
        page_reader_fn=reader_for([result(1), result(2), result(3)]))
    old = sorted(await _chunks_of(db_session, doc), key=lambda c:c.chunk_index)[1]
    old.content = 'teacher correction'; old.edited_by_teacher = True
    await db_session.commit()
    old_id = old.id
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
        page_reader_fn=reader_for([result(1, 'new first'), result(3)], failed=(2,)))
    chunks = sorted(await _chunks_of(db_session, doc), key=lambda c:c.chunk_index)
    assert doc.status.value == 'partially_indexed'
    assert doc.page_count == 3
    assert [c.page_no for c in chunks] == [1,2,3]
    assert chunks[1].id == old_id and chunks[1].content == 'teacher correction'
    assert doc.ingest_report['page_summary']['retained'] == [2]


@pytest.mark.asyncio
async def test_new_partial_document_and_file_change(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)
    doc.storage_path += '.pdf'; await db_session.commit()
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
        page_reader_fn=reader_for([result(1), result(3)], failed=(2,)))
    assert doc.status.value == 'partially_indexed'
    assert doc.ingest_report['page_summary']['missing'] == [2]
    before = [(c.id, c.content) for c in await _chunks_of(db_session, doc)]
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
        page_reader_fn=reader_for([result(1, 'wrong file')], failed=(2,3), digest='changed'))
    assert [(c.id,c.content) for c in await _chunks_of(db_session, doc)] == before
    assert 'file' in doc.error_message.lower()


def test_fingerprint_ignores_visual_text_heading_and_order():
    ref = {'version': 1, 'file_sha256': 'abc', 'page_no': 1, 'ids': ['b','a'], 'regions': [], 'part': 0, 'parts': 1}
    a = Chunk('2^{3}', 1, 'model heading', source_text='23', source_ref=ref)
    b = replace(a, content='2^3', section='another heading', source_ref={**ref, 'ids': ['a','b']})
    assert _source_fingerprint(a) == _source_fingerprint(b)
    assert _source_fingerprint(a) != _source_fingerprint(replace(a, source_ref={**ref, 'regions': ['image']}))

@pytest.mark.asyncio
async def test_reader_continues_after_middle_page_error(tmp_path, monkeypatch):
    import pymupdf
    import backend.worker.page_reader as m
    path=tmp_path/'three.pdf'
    with pymupdf.open() as pdf:
        for n in range(3): pdf.new_page().insert_text((20,40), f'page {n}')
        pdf.save(path)
    seen=[]
    async def read(page, **kwargs):
        seen.append(page.page_no)
        if page.page_no==2: raise m.PageReadError('bad middle')
        return result(page.page_no)
    monkeypatch.setattr(m, 'read_visual_page', read, raising=False)
    pages, report = await m.make_page_reader(None, 'test')(str(path))
    assert seen == [1,2,3]
    assert [p.source.page_no for p in pages] == [1,3]
    assert report['page_count'] == 3
    assert report['page_results'][1]['status'] == 'needs_review'


def test_native_chunks_carry_source_not_heading():
    from backend.worker.page_reader import chunk_read_pages
    c=chunk_read_pages([result(2)])[0]
    assert c.source_text == 'source'
    assert c.source_ref['ids'] == ['p2l0']

@pytest.mark.asyncio
async def test_visual_output_change_keeps_teacher_edit(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path); doc.storage_path+='.pdf'; await db_session.commit()
    a=result(1,'23'); a=replace(a,groups=(ReadGroup('formula',('p1l0',),'2^{3}'),))
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([a],failed=(2,3)))
    old=(await _chunks_of(db_session,doc))[0]; old.content='teacher formula'; old.edited_by_teacher=True
    await db_session.commit(); identity=old.id
    b=replace(a,groups=(ReadGroup('formula',('p1l0',),'2^3'),))
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([b],failed=(2,3)))
    updated=(await _chunks_of(db_session,doc))[0]
    assert updated.id==identity and updated.content=='teacher formula'
    assert updated.source_text=='23'
    assert doc.ingest_report['page_summary']['updated']==[1]


@pytest.mark.asyncio
async def test_teacher_conflict_only_retains_affected_page(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path); doc.storage_path+='.pdf'; await db_session.commit()
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([result(1),result(2),result(3)]))
    old=next(c for c in await _chunks_of(db_session,doc) if c.page_no==2)
    old.content='teacher';old.edited_by_teacher=True;await db_session.commit()
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([result(1,'updated'),result(2,'changed source'),result(3)]))
    assert doc.ingest_report['page_summary']['updated']==[1,3]
    assert doc.ingest_report['page_summary']['retained']==[2]


@pytest.mark.asyncio
async def test_embedding_timeout_is_page_local(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path); doc.storage_path+='.pdf'; await db_session.commit()
    async def embed(texts):
        if 'fail' in texts[0]: raise TimeoutError('embedding timeout')
        return await fake_embed(texts)
    await ingest_document(db_session,doc.id,embed_fn=embed,page_reader_fn=reader_for([result(1),result(2,'fail'),result(3)]))
    assert doc.ingest_report['page_summary']['missing']==[2]
    assert doc.ingest_report['page_summary']['updated']==[1,3]


@pytest.mark.asyncio
async def test_reader_breaker_and_unexpected_error(tmp_path,monkeypatch):
    import pymupdf
    import backend.worker.page_reader as m
    path=tmp_path/'six.pdf'
    with pymupdf.open() as pdf:
        for n in range(6): pdf.new_page().insert_text((20,40),'text')
        pdf.save(path)
    seen=[]
    async def read(page,**kwargs):
        seen.append(page.page_no)
        if page.page_no>1: raise TimeoutError('offline')
        return result(1)
    monkeypatch.setattr(m,'read_visual_page',read)
    pages,report=await m.make_page_reader(None,'test')(str(path))
    assert seen==[1,2,3,4] and len(pages)==1
    assert report['page_results'][-1]['status']=='not_processed'
    async def broken(*args,**kwargs): raise RuntimeError('program bug')
    monkeypatch.setattr(m,'read_visual_page',broken)
    with pytest.raises(RuntimeError,match='program bug'): await m.make_page_reader(None,'test')(str(path))


@pytest.mark.asyncio
async def test_partial_pages_retrievable_with_tenant_filter(pg_session,tmp_path):
    from backend.tests.test_integration_rag import _seed_lab, _fixed_embed
    from backend.app.services.document_service import create_document
    from backend.app.services.retrieval_service import bm25_search
    teacher,cls,lab=await _seed_lab(pg_session)
    _,foreign,otherlab=await _seed_lab(pg_session)
    doc=await create_document(pg_session,class_id=cls.id,lab_id=lab.id,filename='partial.pdf',content=b'test',uploaded_by=teacher.id,storage_root=tmp_path)
    await pg_session.commit()
    await ingest_document(pg_session,doc.id,embed_fn=_fixed_embed,page_reader_fn=reader_for([result(1,'allocation memoire')],failed=(2,3)))
    hits=await bm25_search(pg_session,'allocation',lab.id,class_id=cls.id)
    assert hits and hits[0].page_no==1
    assert not await bm25_search(pg_session,'allocation',otherlab.id,class_id=foreign.id)

@pytest.mark.asyncio
async def test_teacher_edit_during_embedding_preserves_page(pg_session,tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy import select
    from backend.app.models import DocChunk
    from backend.tests.test_integration_rag import _seed_lab, _fixed_embed
    from backend.app.services.document_service import create_document, get_chunk_in_document, update_chunk
    teacher,cls,lab=await _seed_lab(pg_session)
    doc=await create_document(pg_session,class_id=cls.id,lab_id=lab.id,filename='race.pdf',content=b'test',uploaded_by=teacher.id,storage_root=tmp_path)
    await pg_session.commit()
    await ingest_document(pg_session,doc.id,embed_fn=_fixed_embed,page_reader_fn=reader_for([result(1)],failed=(2,3)))
    old=await pg_session.scalar(select(DocChunk).where(DocChunk.document_id==doc.id))
    chunk_id=old.id
    factory=async_sessionmaker(pg_session.bind,expire_on_commit=False)
    async def race(texts):
        async with factory() as other:
            row=await get_chunk_in_document(other,doc.id,chunk_id)
            await update_chunk(other,row,content='new teacher edit',embed_fn=_fixed_embed)
            await other.commit()
        return await _fixed_embed(texts)
    await ingest_document(pg_session,doc.id,embed_fn=race,page_reader_fn=reader_for([result(1)],failed=(2,3)))
    await pg_session.refresh(old)
    assert old.content=='new teacher edit'
    assert doc.ingest_report['page_summary']['retained']==[1]


@pytest.mark.asyncio
async def test_commit_failure_restores_old_index(db_session,tmp_path,monkeypatch):
    from sqlalchemy.exc import OperationalError
    doc=await _seed_document(db_session,tmp_path);doc.storage_path+='.pdf';await db_session.commit()
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([result(1)],failed=(2,3)))
    identity=(await _chunks_of(db_session,doc))[0].id
    doc_id=doc.id
    commit=db_session.commit;called=False
    async def fail_once():
        nonlocal called
        if not called:
            called=True
            raise OperationalError('COMMIT',{},Exception('injected database failure'))
        return await commit()
    monkeypatch.setattr(db_session,'commit',fail_once)
    with pytest.raises(OperationalError):
        await ingest_document(db_session,doc_id,embed_fn=fake_embed,page_reader_fn=reader_for([result(1,'new')],failed=(2,3)))
    await db_session.refresh(doc)
    chunks=await _chunks_of(db_session,doc)
    assert len(chunks)==1 and chunks[0].id==identity and chunks[0].content=='source'


def test_split_chunks_have_shared_group_and_neighbors():
    from backend.worker.page_reader import chunk_read_pages
    chunks=chunk_read_pages([result(1,('a long line\n'*200))])
    assert len(chunks)>1
    assert chunks[0].source_ref['group_id']==chunks[1].source_ref['group_id']
    assert chunks[0].source_ref['next_part']==1
    assert chunks[1].source_ref['previous_part']==0


@pytest.mark.asyncio
async def test_teacher_api_reports_partial_pages(db_session,tmp_path):
    from backend.tests.conftest import make_client
    from backend.app.models import Class,User
    doc=await _seed_document(db_session,tmp_path);doc.storage_path+='.pdf';await db_session.commit()
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([result(1)],failed=(2,3)))
    cls=await db_session.get(Class,doc.class_id)
    teacher=await db_session.get(User,cls.teacher_id)
    client=await make_client(db_session,teacher)
    try:
        response=await client.get(f'/api/teacher/labs/{doc.lab_id}/documents')
        assert response.status_code==200
        body=response.json()[0]
        assert body['status']=='partially_indexed'
        assert body['ingest_report']['page_summary']['missing']==[2,3]
    finally:await client.aclose()

@pytest.mark.asyncio
async def test_malformed_embedding_keeps_other_pages(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path);doc.storage_path+='.pdf';await db_session.commit()
    async def embed(texts):
        return [] if texts[0]=='bad vector' else await fake_embed(texts)
    await ingest_document(db_session,doc.id,embed_fn=embed,page_reader_fn=reader_for([result(1),result(2,'bad vector'),result(3)]))
    assert doc.ingest_report['page_summary']['updated']==[1,3]
    assert doc.ingest_report['page_summary']['missing']==[2]


def test_source_text_uses_original_line_order_not_lexical_ids():
    from backend.worker.page_reader import chunk_read_pages
    lines=tuple(NativeLine(f'p1l{i}',(10,10+i*10,100,20+i*10),str(i)) for i in range(12))
    p=ReadPage(NativePage(1,600,400,lines),(ReadGroup('text',tuple(l.id for l in reversed(lines)),'model reading order'),),1)
    chunk=chunk_read_pages([p])[0]
    assert chunk.source_text=='\n'.join(str(i) for i in range(12))


def test_source_text_alone_does_not_hash_visual_wording():
    a=Chunk('2^{3}',1,'first generated title',source_text='23')
    b=replace(a,content='2^3',section='second generated title')
    assert _source_fingerprint(a)==_source_fingerprint(b)


@pytest.mark.asyncio
async def test_v4_migrates_uniquely_proven_v1_teacher_source(db_session,tmp_path):
    doc=await _seed_document(db_session,tmp_path);doc.storage_path+='.pdf';await db_session.commit()
    old_page=result(1,'23')
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([old_page],failed=(2,3)))
    old=(await _chunks_of(db_session,doc))[0];old.content='teacher exact';old.edited_by_teacher=True
    await db_session.commit();identity=old.id
    new_page=replace(old_page,reader_version='visual-harness-v4.0')
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([new_page],failed=(2,3)))
    updated=(await _chunks_of(db_session,doc))[0]
    assert updated.id==identity and updated.content=='teacher exact'
    assert updated.source_ref['version']==2


@pytest.mark.asyncio
async def test_uncertain_discovered_source_never_rematches_teacher(db_session,tmp_path):
    from backend.worker.page_reader import NativeRegion
    doc=await _seed_document(db_session,tmp_path);doc.storage_path+='.pdf';await db_session.commit()
    p=result(1,'23');p=replace(p,source=replace(p.source,regions=(NativeRegion('d',(1,1,90,30),'discovered'),)),
        groups=(ReadGroup('formula',(), '2^3',('d',)),),reader_version='visual-harness-v4.0')
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([p],failed=(2,3)))
    old=(await _chunks_of(db_session,doc))[0];old.content='teacher exact';old.edited_by_teacher=True;await db_session.commit()
    await ingest_document(db_session,doc.id,embed_fn=fake_embed,page_reader_fn=reader_for([p],failed=(2,3)))
    assert doc.ingest_report['page_summary']['retained']==[1]


# --- v3 sources: reruns never overwrite unresolved reviews or teacher work --

from dataclasses import asdict
from sqlalchemy import select
from backend.app.models import DocumentReview
from backend.tests.test_worker_source_integrity import V15, CODE, PICTURE, native_pages


async def visual_document(session, tmp_path):
    import hashlib
    doc = await _seed_document(session, tmp_path)
    path, pages = native_pages(tmp_path)
    doc.storage_path = str(path)
    doc.content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    await session.commit()
    return doc, pages


def v15(page, *groups):
    """A v1.5 page: the given visual groups plus every other line copied by PDF block."""
    from backend.worker.region_reader import copy_native_groups
    used = {i for g in groups for i in g.ids}
    rest = copy_native_groups(page, [l.id for l in page.lines if l.id not in used])
    order = {l.id: i for i, l in enumerate(page.lines)}
    ordered = sorted((*groups, *rest), key=lambda g: min((order[i] for i in g.ids), default=len(order)))
    return ReadPage(page, tuple(ordered), 1, reader_version=V15)


def figure(page, target, description):
    from backend.worker.page_reader import _native_group_text
    labels = _native_group_text([l for l in page.lines if l.id in target['ids']], 'code') if target['ids'] else ''
    return ReadGroup('figure', tuple(target['ids']), description + ('\n' + labels if labels else ''),
                     tuple(target['region_ids']))


def native_range(page, target):
    from backend.worker.region_reader import _native
    return _native(target, page)


def review_item(doc, page, target, reason='Arrow unclear', code='content_review'):
    from backend.worker.region_reader import make_review
    r = make_review(page, target, reason=reason, reason_code=code, file_sha256=doc.content_hash)
    return {**asdict(r), 'file_sha256': doc.content_hash}


def v15_report(doc, read_pages, reviews=()):
    reviews = list(reviews)
    return {'version': V15, 'source_sha256': doc.content_hash, 'page_count': 2, 'reviews': reviews,
            'page_results': [{'page_no': p.source.page_no,
                              'status': 'partial' if any(r['page_no'] == p.source.page_no for r in reviews) else 'read'}
                             for p in read_pages]}


async def ingest_v15(session, doc, read_pages, reviews=(), embed=fake_embed):
    from backend.worker.partial_ingest import ingest_pages
    await ingest_pages(session, doc, list(read_pages), v15_report(doc, read_pages, reviews),
                       embed_fn=embed, context_fn=None, token_budget=None)


def owner(chunks, line_id):
    return next(c for c in chunks if line_id in c.source_ref.get('ids', []) and c.source_ref.get('scope') != 'page')


@pytest.mark.asyncio
async def test_pending_review_missing_from_rerun_still_protects_old_content(db_session, tmp_path):
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    await ingest_v15(db_session, doc, [v15(p1, native_range(p1, PICTURE)), v15(p2)], [review_item(doc, p1, PICTURE)])
    old = owner(await _chunks_of(db_session, doc), 'p1l4')
    old_id, old_content = old.id, old.content
    # The next run no longer reports the review and supplies a new visual result.
    await ingest_v15(db_session, doc, [v15(p1, figure(p1, PICTURE, 'The node points to the next node.')), v15(p2)])
    chunks = await _chunks_of(db_session, doc)
    kept = owner(chunks, 'p1l4')
    assert (kept.id, kept.content) == (old_id, old_content)
    assert not any('points to' in c.content for c in chunks)
    assert (await db_session.scalars(select(DocumentReview))).one().status == 'pending'


@pytest.mark.asyncio
async def test_model_wording_change_keeps_teacher_edit_and_id(db_session, tmp_path):
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    await ingest_v15(db_session, doc, [v15(p1, figure(p1, PICTURE, 'First wording.')), v15(p2)])
    old = owner(await _chunks_of(db_session, doc), 'p1l4')
    old.content = 'Teacher wording.'; old.edited_by_teacher = True
    await db_session.commit()
    identity = old.id
    await ingest_v15(db_session, doc, [v15(p1, figure(p1, PICTURE, 'Second, different wording.')), v15(p2)])
    kept = owner(await _chunks_of(db_session, doc), 'p1l4')
    assert (kept.id, kept.content) == (identity, 'Teacher wording.')
    assert kept.source_ref['version'] == 3
    assert doc.ingest_report['page_summary']['updated'] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['split', 'merge', 'resplit'])
async def test_regrouped_or_resplit_source_never_moves_teacher_edit(db_session, tmp_path, change):
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    long = lambda word: ''.join(f'{word} sentence {i} about the linked list.\n' for i in range(80))
    first = figure(p1, PICTURE, long('First') if change == 'resplit' else 'Two linked nodes.')
    await ingest_v15(db_session, doc, [v15(p1, first), v15(p2)])
    rows = sorted([c for c in await _chunks_of(db_session, doc) if 'p1l4' in c.source_ref['ids']],
                  key=lambda c: c.source_ref['part'])
    edited = rows[-1]
    edited.content = 'Teacher part.'; edited.edited_by_teacher = True
    await db_session.commit()
    identity = edited.id
    if change == 'split':
        groups = [figure(p1, {'ids': ['p1l4'], 'region_ids': ['p1r0']}, 'One node.'),
                  native_range(p1, {'ids': ['p1l5'], 'region_ids': []})]
    elif change == 'merge':
        groups = [figure(p1, {'ids': ['p1l4', 'p1l5', 'p1l6'], 'region_ids': ['p1r0']}, 'Nodes and a number.')]
    else:
        groups = [figure(p1, PICTURE, long('Second'))]
    await ingest_v15(db_session, doc, [v15(p1, *groups), v15(p2)])
    kept = next(c for c in await _chunks_of(db_session, doc) if c.id == identity)
    assert kept.content == 'Teacher part.'
    assert doc.ingest_report['page_summary']['retained'] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['range', 'page'])
async def test_ignored_visual_is_not_restored_on_rerun_v3(db_session, tmp_path, scope):
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    target = PICTURE if scope == 'range' else {'ids': [l.id for l in p1.lines], 'region_ids': ['p1r0'], 'scope': 'page'}
    await ingest_v15(db_session, doc, [v15(p1, *([native_range(p1, PICTURE)] if scope == 'range' else [])), v15(p2)],
                     [review_item(doc, p1, target)])
    row = (await db_session.scalars(select(DocumentReview))).one()
    row.status = 'ignored'; row.revision += 1
    await db_session.commit()
    await ingest_v15(db_session, doc, [v15(p1, figure(p1, PICTURE, 'Generated interpretation.')), v15(p2)])
    chunks = await _chunks_of(db_session, doc)
    assert not any('Generated' in c.content for c in chunks)
    assert 'Node' in owner(chunks, 'p1l4').content
    assert row.status == 'ignored'


@pytest.mark.asyncio
async def test_new_review_overlapping_old_review_keeps_page_and_adds_no_task(db_session, tmp_path):
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    small = {'ids': ['p1l4'], 'region_ids': []}
    await ingest_v15(db_session, doc, [v15(p1, native_range(p1, small)), v15(p2)], [review_item(doc, p1, small)])
    before = {(c.id, c.content) for c in await _chunks_of(db_session, doc) if c.page_no == 1}
    await ingest_v15(db_session, doc, [v15(p1, native_range(p1, PICTURE)), v15(p2)], [review_item(doc, p1, PICTURE)])
    assert {(c.id, c.content) for c in await _chunks_of(db_session, doc) if c.page_no == 1} == before
    assert len((await db_session.scalars(select(DocumentReview))).all()) == 1
    page = doc.ingest_report['page_reader']['page_results'][0]
    assert page['status'] == 'retained' and 'review_source_conflict' in page['error']


@pytest.mark.asyncio
async def test_reviews_without_chunks_still_refuse_another_file(db_session, tmp_path):
    from backend.app.services.document_reviews import register_reviews
    from backend.worker.ingest import TeacherEditConflict
    doc, (p1, p2) = await visual_document(db_session, tmp_path)
    await register_reviews(db_session, doc, [review_item(doc, p1, PICTURE)])
    await db_session.commit()
    doc.content_hash = 'b' * 64
    with pytest.raises(TeacherEditConflict, match='Source file changed'):
        await ingest_v15(db_session, doc, [v15(p1), v15(p2)])
    assert not await _chunks_of(db_session, doc)
