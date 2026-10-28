"""M3-B PDF indexing: exact ranges, publication when every page was read, failed embedding."""
import uuid

import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.main import app
from backend.app.models import ClassStudent, DocChunk, Document, DocumentBlock, DocumentPage, DocumentRevision, EMBEDDING_DIM, IngestionJob, JobStatus, Session
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_edits, document_versions
from backend.app.services.retrieval_service import _scope
from backend.tests.conftest import make_client, make_user
from backend.tests.test_pdf_reader_m3a import pdf_bytes, items
from backend.tests.test_structured_ingest import seed
from backend.worker import structured_ingest as si
from backend.worker.structured_index import process_index_job, split_block
from backend.worker.pdf_reader import read_pdf


def test_utf8_chunk_ranges_restore_every_character():
    content=('Définition θ₁ : A → B\n' * 100) + 'fin'
    chunks=split_block(content)
    assert ''.join(c.content for c in chunks)==content
    assert chunks[0].start==0 and chunks[-1].end==len(content)
    assert all(c.end == chunks[i+1].start for i,c in enumerate(chunks[:-1]))
    assert all(c.byte_count <= 600 for c in chunks)
    assert len(chunks)>1


async def _parsed_pdf(db,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'STRUCTURED_INGEST_ENABLED',True)
    monkeypatch.setattr(settings,'PDF_STRUCTURED_INGEST_ENABLED',True)
    storage=tmp_path/'storage';storage.mkdir()
    app.dependency_overrides[get_storage_root]=lambda:str(storage)
    teacher,_,lab,_=await seed(db)
    client=await make_client(db,teacher)
    async with client:
        response=await client.post(f'/api/teacher/labs/{lab.id}/documents',
            files={'file':('lecture.pdf',pdf_bytes(),'application/pdf')})
    assert response.status_code==201,response.text
    body=response.json()
    def fake_pipeline(source,digest,work_dir,*,settings,extractor=None,on_page=None):
        doc,metrics=read_pdf(source,digest,work_dir,items)
        # Long block also checks split ranges in the database.
        doc.pages[0].blocks[0].content = 'Définition θ₁ et texte visible\n' * 35
        # The caption step's result; MinerU's own picture text stays unsearched.
        doc.pages[0].blocks[1].caption = 'Schéma avec deux boîtes reliées par une flèche'
        doc.pages[0].blocks[1].caption_source = {'model': 'test', 'prompt_version': 'v1'}
        return si.ParseOutcome(doc,{'status':'source_pdf','extractor':metrics},
                               {'kind':'same_pdf_physical_page','pages':1},True)
    monkeypatch.setattr(si,'run_pdf_pipeline',fake_pipeline)
    worker=si.StructuredSettings(storage_root=storage,task_root=tmp_path/'tasks')
    assert await si.process_structured_job(db,settings=worker)
    doc=await db.get(Document,uuid.UUID(body['id']))
    rev=await db.get(DocumentRevision,uuid.UUID(body['revision_id']))
    job=await db.get(IngestionJob,uuid.UUID(body['job_id']))
    await db.refresh(job)
    assert (job.task_kind,job.status)==('pdf_index',JobStatus.queued)
    assert rev.processing_state=='parsed' and not rev.index_ready
    return doc,rev,teacher,job


@pytest.mark.asyncio
async def test_index_draft_and_teacher_edit_rebuild(db_session,tmp_path,monkeypatch):
    try:
        db=db_session
        doc,rev,teacher,job=await _parsed_pdf(db,tmp_path,monkeypatch)
        # A populated block held for review must not enter either search index.
        figure=next(b for b in (await db.scalars(select(DocumentBlock).where(
            DocumentBlock.revision_id==rev.id))).all() if b.kind=='figure')
        figure_id=figure.block_id
        figure.index_state='needs_review'
        await db.commit()
        calls=[]
        async def embed(texts):
            calls.extend(texts)
            return [[0.0]*(EMBEDDING_DIM-1)+[1.0] for _ in texts]
        assert await process_index_job(db,embed_fn=embed)
        await db.refresh(rev);await db.refresh(job);await db.refresh(doc);await db.refresh(teacher)
        # Every page was read: the version is published at once. A figure
        # without text is normal in a PDF, not a reason to hold it back.
        assert rev.processing_state=='ready' and rev.index_ready
        assert rev.publication_state=='published' and doc.current_revision_id==rev.id
        assert doc.status.value=='indexed' and doc.error_message is None
        assert job.status==JobStatus.done
        chunks=(await db.scalars(select(DocChunk).where(DocChunk.revision_id==rev.id).order_by(DocChunk.chunk_index))).all()
        assert len(chunks)==len(calls) and len(chunks)>5
        # Its text stays out; its caption is searched apart from the text, as an
        # unchecked model description. MinerU's picture text is never searched.
        described=[c for c in chunks if document_versions.chunk_of_description(c.source_ref)]
        assert [(c.block_id,c.content) for c in described]==[(figure_id,'Schéma avec deux boîtes reliées par une flèche')]
        chunks=[c for c in chunks if c not in described]
        assert all(c.block_id!=figure_id and 'invented arrow' not in c.content for c in chunks)
        blocks={b.block_id:b for b in (await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id==rev.id))).all()}
        assert blocks[figure_id].description_origin=='model'
        assert not document_edits.description_checked(blocks[figure_id])
        assert blocks[figure_id].structure['caption']=={'model': 'test', 'prompt_version': 'v1'}
        assert blocks[figure_id].structure['model_candidate']=='invented arrow'
        for block_id in {c.block_id for c in chunks}:
            block=blocks[block_id]
            parts=[c for c in chunks if c.block_id==block_id]
            assert ''.join(c.content for c in parts)==block.content
            assert all(c.content==block.content[c.content_start:c.content_end] for c in parts)
            assert all(c.page_no==1 and c.source_ref.get('format')=='pdf' for c in parts)
        assert (await db.scalars(_scope(select(DocChunk),doc.lab_id,doc.class_id))).all()
        text_block=next(b for b in blocks.values() if b.kind=='text')
        text_block_id=text_block.block_id
        new=await document_edits.edit_block(db,document_id=doc.id,teacher_id=teacher.id,
            block_id=text_block_id,expected_revision_id=rev.id,
            expected_generation=doc.generation,content='Texte corrigé par enseignant')
        await db.commit()
        new_job=await db.scalar(select(IngestionJob).where(IngestionJob.revision_id==new.id))
        assert new_job.task_kind=='pdf_index' and new_job.status==JobStatus.queued
        assert await process_index_job(db,embed_fn=embed)
        await db.refresh(new);await db.refresh(doc);await db.refresh(rev)
        assert new.index_ready and new.processing_state=='ready'
        assert doc.current_revision_id==new.id
        new_chunks=(await db.scalars(select(DocChunk).where(DocChunk.revision_id==new.id,
                       DocChunk.block_id==text_block_id))).all()
        assert ''.join(c.content for c in new_chunks)=='Texte corrigé par enseignant'
        assert all(c.edited_by_teacher for c in new_chunks)
        # The teacher's edit is what students search now.
        visible=(await db.scalars(_scope(select(DocChunk),doc.lab_id,doc.class_id))).all()
        assert visible and all(c.revision_id==new.id for c in visible)
    finally:
        app.dependency_overrides.pop(get_storage_root,None)


@pytest.mark.asyncio
async def test_unread_page_waits_for_the_teacher(db_session,tmp_path,monkeypatch):
    try:
        db=db_session
        doc,rev,teacher,_=await _parsed_pdf(db,tmp_path,monkeypatch)
        page=await db.scalar(select(DocumentPage).where(DocumentPage.revision_id==rev.id))
        page.state='partial'
        slide_no=page.source_slide_no
        await db.commit()
        async def embed(texts):
            return [[0.0]*(EMBEDDING_DIM-1)+[1.0] for _ in texts]
        assert await process_index_job(db,embed_fn=embed)
        await db.refresh(rev);await db.refresh(doc);await db.refresh(teacher)
        assert rev.processing_state=='partial' and rev.publication_state=='draft'
        assert rev.report['index']['unread_pages']==[slide_no]
        assert doc.current_revision_id is None
        target=dict(document_id=doc.id,revision_id=rev.id,teacher_id=teacher.id,
                    expected_generation=doc.generation)
        with pytest.raises(document_versions.VersionConflict):
            await document_versions.publish_revision(db,**target)
        await db.rollback()
        await document_versions.publish_revision(db,**target,acknowledge_issues=True)
        await db.commit()
        await db.refresh(doc)
        assert doc.status.value=='partially_indexed'
        assert doc.error_message==f'Not read: page {slide_no}. Students cannot find their content.'
    finally:
        app.dependency_overrides.pop(get_storage_root,None)


@pytest.mark.asyncio
async def test_failed_embedding_preserves_parsed_draft(db_session,tmp_path,monkeypatch):
    try:
        db=db_session
        doc,rev,_,job=await _parsed_pdf(db,tmp_path,monkeypatch)
        async def bad(_texts):
            raise RuntimeError('endpoint unavailable')
        assert await process_index_job(db,embed_fn=bad)
        await db.refresh(rev);await db.refresh(job);await db.refresh(doc)
        assert rev.processing_state=='parsed' and not rev.index_ready
        assert job.status==JobStatus.queued and job.error_message=='RuntimeError'
        assert not (await db.scalars(select(DocChunk).where(DocChunk.revision_id==rev.id))).all()
        assert doc.current_revision_id is None
    finally:
        app.dependency_overrides.pop(get_storage_root,None)


@pytest.mark.asyncio
async def test_published_pdf_page_and_original_open_with_session_auth(db_session,tmp_path,monkeypatch):
    try:
        db=db_session
        doc,rev,teacher,_=await _parsed_pdf(db,tmp_path,monkeypatch)
        # A page that was not read keeps the version a draft, hidden from students.
        (await db.scalar(select(DocumentPage).where(DocumentPage.revision_id==rev.id))).state='partial'
        await db.commit()
        async def embed(texts):
            return [[0.0]*(EMBEDDING_DIM-1)+[1.0] for _ in texts]
        assert await process_index_job(db,embed_fn=embed)
        await db.refresh(doc);await db.refresh(rev);await db.refresh(teacher)
        student,outsider=make_user(),make_user()
        db.add_all([student,outsider]);await db.flush()
        db.add(ClassStudent(class_id=doc.class_id,student_id=student.id))
        session=Session(id=uuid.uuid4(),lab_id=doc.lab_id,user_id=student.id)
        db.add(session);await db.commit()
        page=await db.scalar(select(DocumentPage).where(DocumentPage.revision_id==rev.id))
        client=await make_client(db,student)
        base=f'/api/documents/{doc.id}/revisions/{rev.id}'
        async with client:
            hidden=await client.get(f'{base}/pages/{page.page_id}',params={'session_id':str(session.id)})
        assert hidden.status_code==404
        await document_versions.publish_revision(db,document_id=doc.id,revision_id=rev.id,
            teacher_id=teacher.id,expected_generation=doc.generation,acknowledge_issues=True)
        await db.commit()
        client=await make_client(db,student)
        async with client:
            opened=await client.get(f'{base}/pages/{page.page_id}',params={'session_id':str(session.id)})
            pdf=await client.get(f'{base}/preview',params={'session_id':str(session.id)},
                                 headers={'Range':'bytes=0-4'})
        assert opened.status_code==200 and opened.json()['page']['preview_page_no']==1
        assert pdf.status_code==206 and pdf.content==b'%PDF-'
        client=await make_client(db,outsider)
        async with client:
            denied=await client.get(f'{base}/preview',params={'session_id':str(session.id)})
        assert denied.status_code==404
    finally:
        app.dependency_overrides.pop(get_storage_root,None)
