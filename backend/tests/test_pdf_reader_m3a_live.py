"""Explicit M3-A live smoke test. Requires an isolated MinerU model server.

Run with M3A_LIVE=1, PDF_MINERU_PYTHON, PDF_MINERU_SERVER_URL,
PDF_MINERU_CONFIG, HF_HOME and no production database. Default test runs skip it.
"""
import os
import uuid

import pymupdf
import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.main import app
from backend.app.models import DocumentBlock, DocumentPage, DocumentRevision
from backend.app.routers.teacher import get_storage_root
from backend.tests.conftest import make_client
from backend.tests.test_structured_ingest import seed
from backend.worker import structured_ingest as si


@pytest.mark.asyncio
async def test_real_mineru_pdf_reaches_teacher_draft(db_session,tmp_path,monkeypatch):
    if os.environ.get('M3A_LIVE')!='1':
        pytest.skip('Set M3A_LIVE=1 and an isolated MinerU service explicitly')
    source='cours/pdf_files/Heritage.pdf'
    with pymupdf.open(source) as original:
        one=pymupdf.open()
        one.insert_pdf(original,from_page=20,to_page=20)
        pdf=one.tobytes()
        one.close()
    monkeypatch.setattr(settings,'STRUCTURED_INGEST_ENABLED',True)
    monkeypatch.setattr(settings,'PDF_STRUCTURED_INGEST_ENABLED',True)
    storage=tmp_path/'storage';storage.mkdir()
    app.dependency_overrides[get_storage_root]=lambda:str(storage)
    try:
        teacher,_,lab,_=await seed(db_session)
        client=await make_client(db_session,teacher)
        async with client:
            response=await client.post(f'/api/teacher/labs/{lab.id}/documents',
                files={'file':('heritage-single-page.pdf',pdf,'application/pdf')})
        assert response.status_code==201,response.text
        body=response.json()
        worker=si.StructuredSettings.from_env(storage)
        worker=si.StructuredSettings(storage_root=storage,task_root=tmp_path/'tasks',
            pdf_python=worker.pdf_python,pdf_server_url=worker.pdf_server_url,
            pdf_config_path=worker.pdf_config_path,pdf_page_timeout_seconds=worker.pdf_page_timeout_seconds)
        assert await si.process_structured_job(db_session,settings=worker)
        rev=await db_session.get(DocumentRevision,uuid.UUID(body['revision_id']))
        await db_session.refresh(rev)
        assert (rev.processing_state,rev.publication_state,rev.index_ready)==('parsed','draft',False),rev.report
        page=await db_session.scalar(select(DocumentPage).where(DocumentPage.revision_id==rev.id))
        assert page.source_slide_no==page.preview_page_no==1
        blocks=(await db_session.scalars(select(DocumentBlock).where(DocumentBlock.revision_id==rev.id))).all()
        assert len(blocks)>=1 and any(b.kind=='formula' for b in blocks)
        assert all(b.source_ref['format']=='pdf' and b.source_ref['source_slide_no']==1 for b in blocks)
        client=await make_client(db_session,teacher)
        async with client:
            read=await client.get(f'/api/teacher/documents/{body["id"]}/revisions/{body["revision_id"]}/read',
                                  params={'page_id':str(page.page_id)})
        assert read.status_code==200,read.text
        assert read.json()['ok'] and any(i['type']=='formula' for i in read.json()['items'])
    finally:
        app.dependency_overrides.pop(get_storage_root,None)
