"""M3-B against isolated pgvector/PostgreSQL; no application database."""
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.main import app
from backend.app.models import DocChunk, Document, DocumentRevision, EMBEDDING_DIM, IngestionJob, JobStatus
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_versions
from backend.app.services.retrieval_service import bm25_search
from backend.tests.test_document_versions_pg import m1_pg
from backend.tests.test_structured_index_m3b import _parsed_pdf
from backend.worker.structured_index import process_index_job


@pytest.mark.asyncio
async def test_pg_pdf_index_and_release_are_revision_scoped(m1_pg,tmp_path,monkeypatch):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    try:
        async with factory() as db:
            doc,rev,teacher,job=await _parsed_pdf(db,tmp_path,monkeypatch)
            ids=(doc.id,rev.id,teacher.id,job.id)
            async def embed(texts):
                return [[0.0]*(EMBEDDING_DIM-1)+[1.0] for _ in texts]
            assert await process_index_job(db,embed_fn=embed)
            doc=await db.get(Document,ids[0],populate_existing=True)
            rev=await db.get(DocumentRevision,ids[1],populate_existing=True)
            job=await db.get(IngestionJob,ids[3],populate_existing=True)
            assert job.status==JobStatus.done and rev.index_ready and rev.processing_state=='partial'
            chunks=(await db.scalars(select(DocChunk).where(DocChunk.revision_id==rev.id))).all()
            assert chunks and all(c.tsv is not None for c in chunks)
            assert not await bm25_search(db,'Définition',doc.lab_id,class_id=doc.class_id)
            await document_versions.publish_revision(db,document_id=doc.id,revision_id=rev.id,
                teacher_id=ids[2],expected_generation=doc.generation,acknowledge_issues=True)
            await db.commit()
            hits=await bm25_search(db,'Définition',doc.lab_id,class_id=doc.class_id)
            assert hits and all(h.document_id==doc.id and h.page_no==1 and
                h.revision_id==rev.id and h.block_id is not None and
                h.content_start is not None and h.content_end is not None for h in hits)
    finally:
        app.dependency_overrides.pop(get_storage_root,None)


@pytest.mark.asyncio
async def test_pg_concurrent_generation_change_rejects_index(m1_pg,tmp_path,monkeypatch):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    try:
        async with factory() as db:
            doc,rev,teacher,job=await _parsed_pdf(db,tmp_path,monkeypatch)
            ids=(doc.id,rev.id,teacher.id,job.id)
            async def embed(texts):
                async with factory() as other:
                    current=await other.get(Document,ids[0])
                    await document_versions.set_archived(other,document_id=ids[0],
                        teacher_id=ids[2],expected_generation=current.generation,archived=True)
                    await other.commit()
                return [[0.0]*(EMBEDDING_DIM-1)+[1.0] for _ in texts]
            assert await process_index_job(db,embed_fn=embed)
            rev=await db.get(DocumentRevision,ids[1],populate_existing=True)
            job=await db.get(IngestionJob,ids[3],populate_existing=True)
            assert rev.processing_state=='parsed' and not rev.index_ready
            assert job.status==JobStatus.queued
            assert not (await db.scalars(select(DocChunk).where(DocChunk.revision_id==rev.id))).all()
    finally:
        app.dependency_overrides.pop(get_storage_root,None)
