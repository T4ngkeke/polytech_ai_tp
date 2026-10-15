"""M3-A PDF queue and parsed draft on an isolated PostgreSQL test schema."""
import hashlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.config import settings
from backend.app.models import DocumentBlock, DocumentPage, DocumentRevision, IngestionJob, JobStatus
from backend.app.services import structured_upload
from backend.tests.test_document_versions_pg import m1_pg  # noqa: F401
from backend.tests.test_structured_ingest_pg import seed
from backend.tests.test_pdf_reader_m3a import items, pdf_bytes
from backend.worker import structured_ingest as si
from backend.worker.pdf_reader import read_pdf


@pytest.mark.asyncio
async def test_pg_pdf_job_is_fenced_and_persists_draft(m1_pg,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'STRUCTURED_INGEST_ENABLED',True)
    monkeypatch.setattr(settings,'PDF_STRUCTURED_INGEST_ENABLED',True)
    storage=tmp_path/'storage';storage.mkdir()
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        teacher,lab=await seed(db)
        content=pdf_bytes()
        result=await structured_upload.upload_pdf(db,teacher_id=teacher.id,lab=lab,shared=False,
            filename='lecture.pdf',content=content,storage_root=storage)
        job=await db.scalar(select(IngestionJob).where(IngestionJob.revision_id==result.revision.id))
        assert job.task_kind=='pdf_parse' and job.status==JobStatus.queued
        def fake_pipeline(source,digest,work_dir,*,settings,extractor=None):
            doc,metrics=read_pdf(source,digest,work_dir,items)
            return si.ParseOutcome(doc,{'status':'source_pdf','extractor':metrics},
                                   {'kind':'same_pdf_physical_page','pages':1},True)
        monkeypatch.setattr(si,'run_pdf_pipeline',fake_pipeline)
        assert await si.process_structured_job(db,settings=si.StructuredSettings(
            storage_root=storage,task_root=tmp_path/'tasks'))
        rev=await db.get(DocumentRevision,result.revision.id)
        await db.refresh(rev)
        assert (rev.processing_state,rev.publication_state,rev.index_ready)==('parsed','draft',False)
        pages=(await db.scalars(select(DocumentPage).where(DocumentPage.revision_id==rev.id))).all()
        blocks=(await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id==rev.id))).all()
        assert len(pages)==1 and pages[0].preview_page_no==1
        assert len(blocks)==5
        assert all(b.source_ref['file_sha256']==hashlib.sha256(content).hexdigest()
                   and b.source_ref['source_slide_no']==1 for b in blocks)
        assert sum(b.kind=='figure' and 'invented arrow' not in b.content for b in blocks)==1
        assert await db.scalar(select(func.count()).select_from(DocumentBlock).where(
            DocumentBlock.revision_id==rev.id,DocumentBlock.index_state=='searchable'))>=1
