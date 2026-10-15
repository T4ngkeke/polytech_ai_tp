"""M3-C: an actual rendered course PDF opens at its cited physical page.

The frozen MinerU content_list is replayed so this checks the application
draft/index/permission/open path without starting a model or production DB.
"""
import hashlib
import json
import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.main import app
from backend.app.models import ClassStudent, Document, DocumentPage, DocumentRevision, EMBEDDING_DIM, Session
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_versions
from backend.tests.conftest import make_client, make_user
from backend.tests.test_structured_ingest import seed
from backend.worker import structured_ingest as si
from backend.worker.pdf_reader import read_pdf
from backend.worker.structured_index import process_index_job

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / 'cours/pdf_files/odp_rendered/De_LAlgebre_Relationnel_au_SQL_2022.pdf'
CACHE = (ROOT / 'documentation/ingestion-agent-acceptance/m3-pdf/2026-09-29-m3c'
         / 'content-list/De_LAlgebre_Relationnel_au_SQL_2022')


@pytest.mark.skipif(not PDF.is_file() or not CACHE.is_dir(),
                    reason='Requires the local M3 course PDF and cached MinerU extraction')
@pytest.mark.asyncio
async def test_real_course_page_nine_auth_and_original_pdf(db_session, tmp_path, monkeypatch):
    db = db_session
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)
    try:
        teacher, _, lab, _ = await seed(db)
        teacher_id = teacher.id
        client = await make_client(db, teacher)
        async with client:
            uploaded = await client.post(f'/api/teacher/labs/{lab.id}/documents',
                                         files={'file': (PDF.name, PDF.read_bytes(), 'application/pdf')})
        assert uploaded.status_code == 201, uploaded.text
        ids = uploaded.json()
        digest = hashlib.sha256(PDF.read_bytes()).hexdigest()

        def cached_page(source, page_no, _work_dir):
            entry = json.loads((CACHE / f'page-{page_no}.json').read_text())
            assert entry['pdf_sha256'] == digest and entry['page_no'] == page_no
            return entry['content_list']

        def cached_pipeline(source, source_digest, work_dir, *, settings):
            parsed, metrics = read_pdf(source, source_digest, work_dir, cached_page)
            return si.ParseOutcome(parsed, {'status': 'source_pdf', 'extractor': metrics},
                                   {'kind': 'same_pdf_physical_page', 'pages': 14}, True)

        monkeypatch.setattr(si, 'run_pdf_pipeline', cached_pipeline)
        worker = si.StructuredSettings(storage_root=storage, task_root=tmp_path / 'tasks')
        assert await si.process_structured_job(db, settings=worker)

        async def embed(texts):
            return [[0.0] * (EMBEDDING_DIM - 1) + [1.0] for _ in texts]

        assert await process_index_job(db, embed_fn=embed)
        doc = await db.get(Document, uuid.UUID(ids['id']), populate_existing=True)
        rev = await db.get(DocumentRevision, uuid.UUID(ids['revision_id']), populate_existing=True)
        document_id, revision_id, generation = doc.id, rev.id, doc.generation
        assert rev.index_ready and len((await db.scalars(select(DocumentPage).where(
            DocumentPage.revision_id == rev.id))).all()) == 14
        student, outsider = make_user(), make_user()
        db.add_all([student, outsider])
        await db.flush()
        db.add(ClassStudent(class_id=doc.class_id, student_id=student.id))
        session = Session(id=uuid.uuid4(), lab_id=doc.lab_id, user_id=student.id)
        db.add(session)
        await db.commit()
        page9 = await db.scalar(select(DocumentPage).where(
            DocumentPage.revision_id == rev.id, DocumentPage.source_slide_no == 9))
        page9_id = page9.page_id
        base = f'/api/documents/{document_id}/revisions/{revision_id}'
        params = {'session_id': str(session.id)}
        client = await make_client(db, student)
        async with client:
            before = await client.get(f'{base}/pages/{page9_id}', params=params)
        assert before.status_code == 404

        await document_versions.publish_revision(db, document_id=document_id, revision_id=revision_id,
                                                 teacher_id=teacher_id, expected_generation=generation,
                                                 acknowledge_issues=True)
        await db.commit()
        client = await make_client(db, student)
        async with client:
            opened = await client.get(f'{base}/pages/{page9_id}', params=params)
            original = await client.get(f'{base}/preview', params=params)
            linked_pdf = await client.get(f'/api/documents/{document_id}/pdf',
                                          params={**params, 'revision_id': str(revision_id)})
        assert opened.status_code == 200
        assert opened.json()['page']['preview_page_no'] == 9
        assert opened.json()['navigation']['next_page_id']
        assert original.status_code == 200
        assert linked_pdf.status_code == 200
        assert linked_pdf.content == original.content
        assert hashlib.sha256(original.content).hexdigest() == digest
        with pymupdf.open(stream=original.content, filetype='pdf') as original_pdf:
            assert len(original_pdf) == 14
            assert 'Restrict(Vin1' in original_pdf[8].get_text()
        client = await make_client(db, outsider)
        async with client:
            denied = await client.get(f'{base}/preview', params=params)
            denied_link = await client.get(f'/api/documents/{document_id}/pdf',
                                           params={**params, 'revision_id': str(revision_id)})
        assert denied.status_code == 404
        assert denied_link.status_code == 404
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_legacy_pdf_opens_only_in_own_session(db_session, tmp_path):
    from backend.app.services.document_service import create_document
    import pymupdf

    with pymupdf.open() as source:
        for number in range(1, 10):
            source.new_page().insert_text((40, 40), f'Course page {number}')
        pdf_bytes = source.tobytes()
    db = db_session
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)
    try:
        _, _, lab, _ = await seed(db)
        student, outsider = make_user(), make_user()
        db.add_all([student, outsider])
        await db.flush()
        db.add(ClassStudent(class_id=lab.class_id, student_id=student.id))
        own = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=student.id)
        other = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=outsider.id)
        db.add_all([own, other])
        await db.flush()
        doc = await create_document(db, class_id=lab.class_id, lab_id=lab.id,
                                    filename='course.pdf', content=pdf_bytes,
                                    uploaded_by=student.id, storage_root=storage)
        url = f'/api/documents/{doc.id}/pdf'
        client = await make_client(db, student)
        async with client:
            opened = await client.get(url, params={'session_id': str(own.id)})
            wrong_session = await client.get(url, params={'session_id': str(other.id)})
        assert opened.status_code == 200 and opened.content == pdf_bytes
        assert wrong_session.status_code == 404
        client = await make_client(db, outsider)
        async with client:
            denied = await client.get(url, params={'session_id': str(other.id)})
        assert denied.status_code == 404
    finally:
        app.dependency_overrides.pop(get_storage_root, None)
