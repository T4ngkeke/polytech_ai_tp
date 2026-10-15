"""Structured PDF jobs on real PostgreSQL: triggers, constraints, SKIP LOCKED and races.

Uses the M1 fixture: a random schema in a disposable *_test database, built by
create_all and then the M1 + M2 migrations. Set M1_TEST_PG_URL explicitly.
"""
import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.models import (
    Class, Document, DocumentPage, DocumentRevision, DocumentStagedObject, IngestionJob, JobStatus,
    Lab, UserRole,
)
from backend.app.services import structured_upload
from backend.app.services.document_cleanup import mark_deleted, run_cleanup
from backend.app.services.document_versions import VersionConflict
from backend.tests import pdf_fixtures
from backend.tests.conftest import make_user
from backend.tests.test_document_versions_pg import m1_pg  # noqa: F401 - fixture
from backend.tests.test_structured_ingest import worker_settings
from backend.worker import structured_ingest as si
from backend.worker.queue import claim_structured_job
from scripts.migrate_document_snapshots import M2_SQL_PATH, apply_migration


async def seed(db):
    teacher = make_user(role=UserRole.teacher)
    db.add(teacher)
    await db.flush()
    cls = Class(id=uuid.uuid4(), name='M2-B pg', teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db.add(cls)
    await db.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name='TP')
    db.add(lab)
    await db.commit()
    return teacher, lab


async def pdf_upload(db, teacher, lab, content, storage):
    return await structured_upload.upload_pdf(
        db, teacher_id=teacher.id, lab=lab, shared=False, filename='cours.pdf', content=content,
        language='fr', storage_root=storage)


@pytest.fixture
def deck(tmp_path):
    return pdf_fixtures.course_pdf(3)


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / 'storage'
    root.mkdir()
    return root


@pytest.mark.asyncio
async def test_pg_m2_migration_is_recorded_and_repeatable(m1_pg):
    async with m1_pg.begin() as conn:
        rows = dict((await conn.execute(text('SELECT version, checksum FROM document_schema_migrations'))).all())
        assert rows['m2_structured_ingest_v1'] == hashlib.sha256(M2_SQL_PATH.read_bytes()).hexdigest()
        assert await apply_migration(conn) == 'already_applied'
        # The statements themselves are repeatable too, not only the ledger.
        for stmt in M2_SQL_PATH.read_text().split('-- statement')[1:]:
            if stmt.strip():
                await conn.execute(text(stmt))


@pytest.mark.asyncio
async def test_pg_a_half_bound_structured_job_is_refused(m1_pg, storage, deck):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    async with factory() as db:
        teacher, lab = await seed(db)
        result = await pdf_upload(db, teacher, lab, deck, storage)
        doc_id, source_id, rev_id = result.document.id, result.revision.source_version_id, result.revision.id
        db.add(IngestionJob(id=uuid.uuid4(), document_id=doc_id, task_kind='pdf_parse'))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        # One structured job per revision.
        db.add(IngestionJob(id=uuid.uuid4(), document_id=doc_id, task_kind='pdf_parse',
                            source_version_id=source_id, revision_id=rev_id))
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_pg_the_structured_claim_never_takes_a_legacy_row(m1_pg, storage, deck):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    async with factory() as db:
        teacher, lab = await seed(db)
        await pdf_upload(db, teacher, lab, deck, storage)
        class_id, lab_id, teacher_id = lab.class_id, lab.id, teacher.id
        legacy = Document(id=uuid.uuid4(), class_id=class_id, lab_id=lab_id, filename='cours.pdf',
                          storage_path='/x.pdf', content_hash='b' * 64, uploaded_by=teacher_id)
        legacy_id = legacy.id
        db.add(legacy)
        await db.flush()
        db.add(IngestionJob(id=uuid.uuid4(), document_id=legacy_id))
        await db.commit()
        claim = await claim_structured_job(db)
        assert claim is not None and claim.document_id != legacy_id
        assert await claim_structured_job(db) is None


@pytest.mark.asyncio
async def test_pg_concurrent_claims_take_different_jobs(m1_pg, storage, deck, tmp_path):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    async with factory() as db:
        teacher, lab = await seed(db)
        other = pdf_fixtures.course_pdf(2, title='Autre')
        await pdf_upload(db, teacher, lab, deck, storage)
        await pdf_upload(db, teacher, lab, other, storage)

    async def claim():
        async with factory() as db:
            return await claim_structured_job(db)

    first, second, third = await asyncio.wait_for(asyncio.gather(claim(), claim(), claim()), 15)
    taken = [c.job_id for c in (first, second, third) if c is not None]
    assert len(taken) == 2 and len(set(taken)) == 2


@pytest.mark.asyncio
async def test_pg_full_job_stores_a_parsed_draft_under_the_triggers(m1_pg, storage, deck, tmp_path):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    async with factory() as db:
        teacher, lab = await seed(db)
        result = await pdf_upload(db, teacher, lab, deck, storage)
        assert await si.process_structured_job(
            db, settings=worker_settings(storage, tmp_path), extractor=pdf_fixtures.extractor())
        rev = await db.get(DocumentRevision, result.revision.id)
        await db.refresh(rev)
        assert (rev.processing_state, rev.publication_state, rev.index_ready) == ('parsed', 'draft', False)
        pages = (await db.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev.id))).all()
        assert len(pages) == 3 and all(p.preview_page_no for p in pages)
        assert await db.scalar(select(func.count()).select_from(DocumentStagedObject)) == 0
        doc = await db.get(Document, result.document.id)
        await db.refresh(doc)
        await mark_deleted(db, document_id=doc.id, teacher_id=teacher.id, expected_generation=doc.generation)
        await db.commit()
        job = await run_cleanup(db, document_id=doc.id, storage_root=storage)
        await db.commit()
        assert job.state == 'complete'
        assert [p for p in storage.rglob('*') if p.is_file()] == []


@pytest.mark.asyncio
async def test_pg_a_commit_racing_a_deletion_never_revives_the_document(m1_pg, storage, deck, tmp_path):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    async with factory() as db:
        teacher, lab = await seed(db)
        result = await pdf_upload(db, teacher, lab, deck, storage)
        claim = await claim_structured_job(db)
        copy = tmp_path / 'copy.pdf'
        copy.write_bytes(deck)
        outcome = si.run_pdf_pipeline(copy, hashlib.sha256(deck).hexdigest(), tmp_path / 'work',
                                      settings=worker_settings(storage, tmp_path), extractor=pdf_fixtures.page_items)
        planned = si.plan_assets(outcome, claim)
        await si._stage(db, claim, planned, worker_settings(storage, tmp_path), si._utcnow)
        doc_id, teacher_id, generation = result.document.id, teacher.id, claim.generation

    async def commit():
        async with factory() as db:
            try:
                await si._commit_parsed(db, claim, outcome, planned, si._utcnow)
                return 'parsed'
            except (si.StaleClaim, VersionConflict, DBAPIError):
                await db.rollback()
                return 'refused'

    async def delete():
        async with factory() as db:
            try:
                await mark_deleted(db, document_id=doc_id, teacher_id=teacher_id, expected_generation=generation)
                await db.commit()
                return 'deleted'
            except (VersionConflict, DBAPIError):
                await db.rollback()
                return 'refused'

    outcomes = await asyncio.wait_for(asyncio.gather(commit(), delete()), 20)
    async with factory() as db:
        doc = await db.get(Document, doc_id)
        if outcomes[1] == 'deleted':
            assert outcomes[0] == 'refused'
            assert doc.lifecycle == 'deleted'
            assert await db.scalar(select(func.count()).select_from(DocumentPage)) == 0
            # The staged files of the refused commit are found by the cleanup.
            staged = (await db.scalars(select(DocumentStagedObject))).all()
            assert staged and all(row.document_id == doc_id for row in staged)
        else:
            # The commit won: generation moved, so the deletion with the old one lost.
            assert outcomes == ['parsed', 'refused'] and doc.lifecycle == 'active'


@pytest.mark.asyncio
async def test_pg_a_commit_behind_a_deletion_writes_nothing(m1_pg, storage, deck, tmp_path):
    """Same race with the order fixed: the deletion holds the lock first."""
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    async with factory() as db:
        teacher, lab = await seed(db)
        result = await pdf_upload(db, teacher, lab, deck, storage)
        claim = await claim_structured_job(db)
        copy = tmp_path / 'copy.pdf'
        copy.write_bytes(deck)
        outcome = si.run_pdf_pipeline(copy, hashlib.sha256(deck).hexdigest(), tmp_path / 'work',
                                      settings=worker_settings(storage, tmp_path), extractor=pdf_fixtures.page_items)
        planned = si.plan_assets(outcome, claim)
        await si._stage(db, claim, planned, worker_settings(storage, tmp_path), si._utcnow)
        doc_id, teacher_id = result.document.id, teacher.id

    async def commit():
        async with factory() as db:
            try:
                await si._commit_parsed(db, claim, outcome, planned, si._utcnow)
                return 'parsed'
            except (si.StaleClaim, VersionConflict, DBAPIError) as exc:
                await db.rollback()
                return f'refused:{exc}'

    async with factory() as deleting:
        await mark_deleted(deleting, document_id=doc_id, teacher_id=teacher_id,
                           expected_generation=claim.generation)
        late = asyncio.create_task(commit())
        await asyncio.sleep(0.5)
        assert not late.done()  # the worker waits on the document lock
        await deleting.commit()
    assert (await asyncio.wait_for(late, 10)) == 'refused:document_deleted'
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(DocumentPage)) == 0
        assert (await db.get(Document, doc_id)).lifecycle == 'deleted'
        # Its files are in the ledger, and the deletion waits for the lease.
        assert await db.scalar(select(func.count()).select_from(DocumentStagedObject)) == len(planned)
        job = await run_cleanup(db, document_id=doc_id, storage_root=storage)
        await db.commit()
        assert job.last_error_code == 'staged_objects_pending'
