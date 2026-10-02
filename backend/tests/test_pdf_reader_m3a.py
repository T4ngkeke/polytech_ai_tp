"""M3-A: page identity and parsed draft for a PDF, without a model service."""
import hashlib
import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import func, select

from backend.app.config import settings
from backend.app.main import app
from backend.app.models import Document, DocumentBlock, DocumentPage, DocumentRevision, IngestionJob
from backend.app.routers.teacher import get_storage_root
from backend.tests.conftest import make_client
from backend.tests.test_structured_ingest import seed
from backend.worker import structured_ingest as si
from backend.worker.pdf_reader import PDFReadError, read_pdf


def pdf_bytes():
    doc=pymupdf.open()
    page=doc.new_page()
    page.insert_text((70,80),'Diagramme Alpha et Beta',fontsize=15)
    page.insert_text((70,130),'texte visible')
    data=doc.tobytes()
    doc.close()
    return data


def items(_path, _number, _output):
    return [
        {'type':'text','text':'texte visible','bbox':[100,120,700,240],'page_idx':0},
        {'type':'image','content':'invented arrow','bbox':[100,20,800,300],'page_idx':0},
        {'type':'code','code_body':'```python\nx = 1\n```','bbox':[100,350,800,450],'page_idx':0},
        {'type':'table','table_body':'<table><tr><td>A</td></tr></table>','bbox':[100,460,800,550],'page_idx':0},
        {'type':'equation','text':'$x_1$','text_format':'latex','bbox':[100,600,800,700],'page_idx':0},
    ]


def test_pdf_reader_keeps_native_labels_and_page_source(tmp_path):
    path=tmp_path/'lecture.pdf';path.write_bytes(pdf_bytes())
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    doc,metrics=read_pdf(path,digest,tmp_path/'work',items)
    page=doc.pages[0]
    assert page.source_slide_no==page.preview_page_no==1
    assert page.preview_mapping_check=='same_pdf_page'
    assert doc.asset_by_id(page.preview_asset_id).sha256
    assert [b.kind for b in page.blocks]==['text','figure','code','table','formula']
    figure=page.blocks[1]
    assert 'Diagramme Alpha' in figure.content and 'invented arrow' not in figure.content
    assert figure.source_ref.file_sha256==digest and figure.source_ref.format=='pdf'
    assert figure.source_ref.bbox is None and figure.source_ref.legacy_ref['mineru_bbox_per_mille']
    assert page.blocks[2].content.startswith('```python')
    assert page.blocks[3].content.startswith('<table>')
    assert page.blocks[4].content_format=='latex'
    assert metrics['model_calls'] is None


def test_bad_page_identity_and_missing_figure_text_are_exposed(tmp_path):
    path=tmp_path/'bad.pdf';path.write_bytes(pdf_bytes())
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    def broken(*_):
        return [{'type':'image','content':'hallucination','bbox':[800,800,900,900],'page_idx':0},
                {'type':'text','text':'wrong page','page_idx':37}]
    doc,_=read_pdf(path,digest,tmp_path/'work',broken)
    page=doc.pages[0]
    assert page.state=='partial'
    assert len(page.blocks)==1 and page.blocks[0].content==''
    assert any(i.code=='source_identity_conflict' for i in page.issues)
    assert any(i.code=='content_review' for i in page.blocks[0].issues)
    with pytest.raises(PDFReadError,match='source_hash_mismatch'):
        read_pdf(path,'0'*64,tmp_path/'other',broken)


@pytest.mark.asyncio
async def test_pdf_upload_and_worker_write_only_a_readable_draft(db_session,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'STRUCTURED_INGEST_ENABLED',True)
    monkeypatch.setattr(settings,'PDF_STRUCTURED_INGEST_ENABLED',True)
    storage=tmp_path/'storage';storage.mkdir()
    app.dependency_overrides[get_storage_root]=lambda:str(storage)
    try:
        teacher,_,lab,_=await seed(db_session)
        client=await make_client(db_session,teacher)
        async with client:
            resp=await client.post(f'/api/teacher/labs/{lab.id}/documents',
                files={'file':('lecture.pdf',pdf_bytes(),'application/pdf')})
        assert resp.status_code==201,resp.text
        client=await make_client(db_session,teacher)
        async with client:
            odp=await client.post(f'/api/teacher/labs/{lab.id}/documents',
                files={'file':('legacy.odp',b'not an ODP','application/vnd.oasis.opendocument.presentation')})
        assert odp.status_code==400 and odp.json()['detail']=='odp_not_supported'
        body=resp.json();assert body['structured'] and body['processing_state']=='queued'
        job=await db_session.get(IngestionJob,uuid.UUID(body['job_id']))
        assert job.task_kind=='pdf_parse'
        real=si.run_pdf_pipeline
        def fake_pipeline(source,digest,work_dir,*,settings):
            doc,metrics=read_pdf(source,digest,work_dir,items)
            return si.ParseOutcome(doc,{'status':'source_pdf','extractor':metrics},
                                   {'kind':'same_pdf_physical_page','pages':1},True)
        monkeypatch.setattr(si,'run_pdf_pipeline',fake_pipeline)
        worker=si.StructuredSettings(storage_root=storage,task_root=tmp_path/'tasks')
        assert await si.process_structured_job(db_session,settings=worker)
        rev=await db_session.get(DocumentRevision,uuid.UUID(body['revision_id']))
        await db_session.refresh(rev)
        assert (rev.processing_state,rev.publication_state,rev.index_ready)==('parsed','draft',False)
        assert rev.preview_pdf_asset_id is not None
        pages=(await db_session.scalars(select(DocumentPage).where(DocumentPage.revision_id==rev.id))).all()
        blocks=(await db_session.scalars(select(DocumentBlock).where(DocumentBlock.revision_id==rev.id))).all()
        assert len(pages)==1 and pages[0].preview_page_no==1
        assert len(blocks)==5 and any(b.kind=='figure' and 'invented arrow' not in b.content for b in blocks)
        assert await db_session.scalar(select(func.count()).select_from(Document).where(Document.current_revision_id==rev.id))==0
        client=await make_client(db_session,teacher)
        async with client:
            read=await client.get(f'/api/teacher/documents/{body["id"]}/revisions/{body["revision_id"]}/read',
                                  params={'page_id':str(pages[0].page_id)})
        assert read.status_code==200,read.text
        result=read.json()
        assert result['ok'] and len(result['items'])==5
        assert any(item['type']=='figure' and item['source']['format']=='pdf' for item in result['items'])
    finally:
        app.dependency_overrides.pop(get_storage_root,None)
