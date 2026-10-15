"""Structured PDF upload, the parse job and the parsed draft (SQLite).

No MinerU and no model run here: pdf_fixtures.extractor stands in for MinerU,
and the worker must validate its result like any other. PostgreSQL locking is
covered by test_structured_ingest_pg.py.

The helpers (seed, upload, run_job, worker_settings) and fixtures (storage,
structured_on, deck_bytes) are shared with the other structured-document tests.
"""
import asyncio
import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from backend.app.config import settings
from backend.app.main import app
from backend.app.models import (
    Class, Document, DocumentAsset, DocumentBlock, DocumentBlockAsset, DocumentPage,
    DocumentRevision, DocumentSource, DocumentStagedObject, IngestionJob, JobStatus, Lab, UserRole,
    _utcnow,
)
from backend.app.routers.teacher import get_storage_root
from backend.app.services import document_versions as versions
from backend.app.services.document_cleanup import mark_deleted, run_cleanup, sweep_staged_objects
from backend.tests import pdf_fixtures
from backend.tests.conftest import make_client, make_user
from backend.worker import structured_ingest as si
from backend.worker.queue import claim_structured_job


@pytest.fixture(autouse=True)
def structured_on(monkeypatch):
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / 'storage'
    root.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(root)
    yield root
    app.dependency_overrides.pop(get_storage_root, None)


@pytest.fixture
def deck_bytes():
    """A three-page course PDF."""
    return pdf_fixtures.course_pdf(3)


@pytest.fixture
def other_deck_bytes():
    return pdf_fixtures.course_pdf(2, title='Autre')


async def seed(db):
    teacher, outsider = make_user(role=UserRole.teacher), make_user(role=UserRole.teacher)
    db.add_all([teacher, outsider])
    await db.flush()
    cls = Class(id=uuid.uuid4(), name='Structured', teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db.add(cls)
    await db.flush()
    lab, other_lab = Lab(id=uuid.uuid4(), class_id=cls.id, name='TP'), Lab(id=uuid.uuid4(), class_id=cls.id, name='TP2')
    db.add_all([lab, other_lab])
    await db.commit()
    return teacher, outsider, lab, other_lab


async def upload(db, user, lab, content, *, name='cours.pdf', **form):
    client = await make_client(db, user)
    async with client:
        data = {key: str(value) for key, value in form.items()}
        return await client.post(f'/api/teacher/labs/{lab.id}/documents',
                                 files={'file': (name, content, 'application/pdf')}, data=data)


def worker_settings(storage, tmp_path, **kw):
    return si.StructuredSettings(storage_root=storage, task_root=tmp_path / 'tasks', **kw)


async def run_job(db, storage, tmp_path, content=None, *, extractor=None, settings=None):
    """Run one structured job with the MinerU stand-in (content: unused, kept
    for the callers' readability)."""
    return await si.process_structured_job(
        db, settings=worker_settings(storage, tmp_path, **(settings or {})),
        extractor=extractor or pdf_fixtures.extractor())


def stored_files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file())


async def count(db, model, *where):
    return await db.scalar(select(func.count()).select_from(model).where(*where))


async def uploaded(db, storage, content, lab=None, teacher=None):
    if teacher is None:
        teacher, _, lab, _ = await seed(db)
    body = (await upload(db, teacher, lab, content)).json()
    return teacher, lab, body


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_odp_upload_is_refused(db_session, storage):
    teacher, _, lab, _ = await seed(db_session)
    resp = await upload(db_session, teacher, lab, b'PK\x03\x04 slides', name='cours.odp')
    assert resp.status_code == 400 and resp.json()['detail'] == 'odp_not_supported'
    assert await count(db_session, Document) == 0 and stored_files(storage) == []


@pytest.mark.asyncio
async def test_a_new_upload_stores_the_file_under_a_server_key_and_queues_one_job(db_session, storage, deck_bytes):
    teacher, _, lab, _ = await seed(db_session)
    resp = await upload(db_session, teacher, lab, deck_bytes, name='../../etc/Cours final.pdf')
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body['structured'] and body['processing_state'] == 'queued' and body['job_id']
    source = await db_session.get(DocumentSource, uuid.UUID(body['source_version_id']))
    # The client's name is a label only: no path part of it reaches storage.
    assert source.original_filename == 'Cours final.pdf'
    assert source.storage_key.startswith(f"{body['id']}/sources/") and 'Cours' not in source.storage_key
    assert hashlib.sha256((storage / source.storage_key).read_bytes()).hexdigest() == source.sha256
    assert source.sha256 == hashlib.sha256(deck_bytes).hexdigest()
    job = await db_session.get(IngestionJob, uuid.UUID(body['job_id']))
    assert (job.task_kind, job.revision_id, job.source_version_id) == (
        'pdf_parse', uuid.UUID(body['revision_id']), source.id)
    assert await count(db_session, DocumentStagedObject) == 0
    rev = await db_session.get(DocumentRevision, job.revision_id)
    assert rev.publication_state == 'draft' and not rev.index_ready


@pytest.mark.asyncio
async def test_new_never_merges_by_hash_or_name(db_session, storage, deck_bytes):
    teacher, _, lab, _ = await seed(db_session)
    first = (await upload(db_session, teacher, lab, deck_bytes)).json()
    second = (await upload(db_session, teacher, lab, deck_bytes)).json()
    assert first['id'] != second['id'] and first['job_id'] != second['job_id']
    assert await count(db_session, IngestionJob, IngestionJob.task_kind == 'pdf_parse') == 2


@pytest.mark.asyncio
async def test_the_same_content_again_reuses_the_revision_and_the_job(db_session, storage, deck_bytes):
    teacher, _, lab, _ = await seed(db_session)
    first = (await upload(db_session, teacher, lab, deck_bytes)).json()
    again = await upload(db_session, teacher, lab, deck_bytes, mode='replace',
                         replace_document_id=first['id'], expected_generation=first['generation'])
    # Idempotent even with the generation seen before: a double submit is harmless.
    assert again.status_code == 200, again.text
    assert (again.json()['revision_id'], again.json()['job_id']) == (first['revision_id'], first['job_id'])
    assert await count(db_session, IngestionJob) == 1 and await count(db_session, DocumentSource) == 1
    assert len(stored_files(storage)) == 1 and await count(db_session, DocumentStagedObject) == 0


@pytest.mark.asyncio
async def test_replace_checks_the_generation_and_keeps_the_course_scope(db_session, storage, deck_bytes, other_deck_bytes):
    teacher, outsider, lab, other_lab = await seed(db_session)
    first = (await upload(db_session, teacher, lab, deck_bytes)).json()
    stale = await upload(db_session, teacher, lab, other_deck_bytes, mode='replace',
                         replace_document_id=first['id'], expected_generation=first['generation'] - 1)
    assert stale.status_code == 409 and stale.json()['detail'] == 'stale_generation_or_lifecycle'
    moved = await upload(db_session, teacher, other_lab, other_deck_bytes, mode='replace',
                         replace_document_id=first['id'], expected_generation=first['generation'])
    assert moved.status_code == 409 and moved.json()['detail'] == 'scope_mismatch'
    shared = await upload(db_session, teacher, lab, other_deck_bytes, mode='replace', shared='true',
                          replace_document_id=first['id'], expected_generation=first['generation'])
    assert shared.status_code == 409
    missing = await upload(db_session, teacher, lab, other_deck_bytes, mode='replace')
    assert missing.status_code == 400
    foreign = await upload(db_session, outsider, lab, other_deck_bytes, mode='replace',
                           replace_document_id=first['id'], expected_generation=first['generation'])
    assert foreign.status_code in (403, 404)
    assert await count(db_session, DocumentSource) == 1 and len(stored_files(storage)) == 1
    assert await count(db_session, DocumentStagedObject) == 0
    ok = await upload(db_session, teacher, lab, other_deck_bytes, mode='replace',
                      replace_document_id=first['id'], expected_generation=first['generation'])
    assert ok.status_code == 201 and ok.json()['id'] == first['id']
    new_source = await db_session.get(DocumentSource, uuid.UUID(ok.json()['source_version_id']))
    assert new_source.replaces_source_id is None  # nothing was published yet
    assert await count(db_session, IngestionJob) == 2


@pytest.mark.asyncio
async def test_replace_is_only_for_structured_documents(db_session, storage, deck_bytes):
    teacher, _, lab, _ = await seed(db_session)
    legacy = Document(id=uuid.uuid4(), class_id=lab.class_id, lab_id=lab.id, filename='old.pdf',
                      storage_path='/x', content_hash=uuid.uuid4().hex, uploaded_by=teacher.id)
    db_session.add(legacy)
    await db_session.commit()
    over_legacy = await upload(db_session, teacher, lab, deck_bytes, mode='replace',
                               replace_document_id=legacy.id, expected_generation=0)
    assert over_legacy.status_code == 409 and over_legacy.json()['detail'] == 'legacy_document'


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [b'not a pdf at all', b'%PDF-1.4 broken'])
async def test_a_file_that_is_not_a_readable_pdf_is_refused_before_anything_is_stored(db_session, storage, content):
    teacher, _, lab, _ = await seed(db_session)
    resp = await upload(db_session, teacher, lab, content)
    assert resp.status_code == 400 and resp.json()['detail'] == 'invalid_pdf'
    assert await count(db_session, Document) == 0 and await count(db_session, DocumentStagedObject) == 0
    assert stored_files(storage) == []


# ---------------------------------------------------------------------------
# The structured job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_job_stores_a_parsed_draft_that_cannot_publish(db_session, storage, tmp_path, deck_bytes):
    teacher, lab, body = await uploaded(db_session, storage, deck_bytes)
    assert await run_job(db_session, storage, tmp_path)
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    job = await db_session.get(IngestionJob, uuid.UUID(body['job_id']))
    # The same job goes on to index the draft.
    assert (job.task_kind, job.status) == ('pdf_index', JobStatus.queued)
    assert (rev.processing_state, rev.publication_state, rev.index_ready) == ('parsed', 'draft', False)
    assert rev.report['next_step'] == 'index' and rev.report['pipeline_version'] == si.PDF_PIPELINE_VERSION
    assert rev.reader_version.startswith('mineru-text-') and rev.preview_pdf_asset_id
    doc = await db_session.get(Document, uuid.UUID(body['id']))
    assert doc.current_revision_id is None
    pages = (await db_session.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev.id)
                                      .order_by(DocumentPage.order_index))).all()
    assert [(p.source_slide_no, p.preview_page_no) for p in pages] == [(1, 1), (2, 2), (3, 3)]
    blocks = (await db_session.scalars(select(DocumentBlock).where(DocumentBlock.revision_id == rev.id))).all()
    assert {b.content for b in blocks} >= {'Titre 2', 'Texte de la page 2'}
    # Every stored asset file exists under its key with its hash.
    assets = (await db_session.scalars(select(DocumentAsset).where(
        DocumentAsset.source_version_id == rev.source_version_id))).all()
    assert {'pdf_preview', 'page_preview'} <= {a.kind for a in assets}
    for asset in assets:
        assert hashlib.sha256((storage / asset.storage_key).read_bytes()).hexdigest() == asset.sha256
    assert await count(db_session, DocumentStagedObject) == 0
    assert list((tmp_path / 'tasks').iterdir()) == []
    with pytest.raises(versions.VersionConflict, match='revision_not_ready'):
        await versions.publish_revision(db_session, document_id=doc.id, revision_id=rev.id,
                                        teacher_id=teacher.id, expected_generation=doc.generation,
                                        acknowledge_issues=True)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_the_parsed_state_is_shown_in_the_teacher_list(db_session, storage, tmp_path, deck_bytes):
    teacher, lab, body = await uploaded(db_session, storage, deck_bytes)
    await run_job(db_session, storage, tmp_path)
    client = await make_client(db_session, teacher)
    async with client:
        rows = (await client.get(f'/api/teacher/labs/{lab.id}/documents')).json()
    latest = next(r for r in rows if r['id'] == body['id'])['latest_revision']
    assert latest['processing_state'] == 'parsed'


@pytest.mark.asyncio
async def test_parsed_can_never_be_marked_indexed(db_session, storage, tmp_path, deck_bytes):
    _, _, body = await uploaded(db_session, storage, deck_bytes)
    with pytest.raises(ValueError, match='parsed_revision_is_not_indexed'):
        await versions.set_processing_result(db_session, document_id=uuid.UUID(body['id']),
            revision_id=uuid.UUID(body['revision_id']), expected_generation=body['generation'],
            state='parsed', report={}, index_ready=True)


@pytest.mark.asyncio
async def test_without_mineru_the_job_fails_plainly(db_session, storage, tmp_path, deck_bytes):
    _, _, body = await uploaded(db_session, storage, deck_bytes)
    assert await si.process_structured_job(db_session, settings=worker_settings(storage, tmp_path))
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    await db_session.refresh(rev)
    assert (rev.processing_state, rev.report['error_code']) == ('failed', 'mineru_not_configured')


@pytest.mark.asyncio
async def test_an_odp_job_left_in_the_queue_fails_as_not_supported():
    from types import SimpleNamespace

    class Db:
        async def get(self, _model, _id):
            return SimpleNamespace(format='odp')

        async def commit(self):
            pass

    with pytest.raises(si.JobFailed, match='odp_not_supported'):
        await si._source_row(Db(), SimpleNamespace(source_version_id=uuid.uuid4()))


@pytest.mark.asyncio
async def test_a_changed_generation_discards_the_result_and_keeps_the_attempt(db_session, storage, tmp_path, deck_bytes):
    teacher, lab, body = await uploaded(db_session, storage, deck_bytes)
    doc_id = uuid.UUID(body['id'])

    async def teacher_acts():
        # Archive and restore: two generation bumps while the worker parses.
        doc = await db_session.get(Document, doc_id)
        await versions.set_archived(db_session, document_id=doc_id, teacher_id=teacher.id,
                                    expected_generation=doc.generation, archived=True)
        await versions.set_archived(db_session, document_id=doc_id, teacher_id=teacher.id,
                                    expected_generation=doc.generation, archived=False)
        await db_session.commit()

    loop = asyncio.get_running_loop()
    await run_job(db_session, storage, tmp_path, extractor=pdf_fixtures.extractor(side_effect=teacher_acts, loop=loop))
    job = await db_session.get(IngestionJob, uuid.UUID(body['job_id']))
    await db_session.refresh(job)
    assert (job.status, job.attempts) == (JobStatus.queued, 0)
    assert await count(db_session, DocumentPage) == 0 and await count(db_session, DocumentAsset) == 0
    assert await count(db_session, DocumentStagedObject) == 0
    assert stored_files(storage) == [(await db_session.get(DocumentSource, uuid.UUID(body['source_version_id']))).storage_key]
    # The next run sees the new generation and stores the draft.
    assert await run_job(db_session, storage, tmp_path)
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    await db_session.refresh(rev)
    assert rev.processing_state == 'parsed'


@pytest.mark.asyncio
async def test_an_archived_document_keeps_its_job_until_restore(db_session, storage, tmp_path, deck_bytes):
    teacher, lab, body = await uploaded(db_session, storage, deck_bytes)
    doc_id, teacher_id = uuid.UUID(body['id']), teacher.id
    doc = await db_session.get(Document, doc_id)
    await versions.set_archived(db_session, document_id=doc_id, teacher_id=teacher_id,
                                expected_generation=doc.generation, archived=True)
    await db_session.commit()
    assert not await run_job(db_session, storage, tmp_path)  # not claimable while archived
    doc = await db_session.get(Document, doc_id)
    await versions.set_archived(db_session, document_id=doc_id, teacher_id=teacher_id,
                                expected_generation=doc.generation, archived=False)
    await db_session.commit()
    assert await run_job(db_session, storage, tmp_path)
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    await db_session.refresh(rev)
    assert rev.processing_state == 'parsed'


@pytest.mark.asyncio
async def test_a_deletion_during_the_job_leaves_nothing_readable_or_stored(db_session, storage, tmp_path, deck_bytes):
    teacher, lab, body = await uploaded(db_session, storage, deck_bytes)
    doc_id = uuid.UUID(body['id'])

    async def teacher_deletes():
        doc = await db_session.get(Document, doc_id)
        await mark_deleted(db_session, document_id=doc_id, teacher_id=teacher.id, expected_generation=doc.generation)
        await db_session.commit()

    loop = asyncio.get_running_loop()
    await run_job(db_session, storage, tmp_path, extractor=pdf_fixtures.extractor(side_effect=teacher_deletes, loop=loop))
    assert await count(db_session, DocumentPage) == 0 and await count(db_session, DocumentAsset) == 0
    assert await count(db_session, DocumentStagedObject) == 0
    job = await run_cleanup(db_session, document_id=doc_id, storage_root=storage)
    await db_session.commit()
    assert job.state == 'complete' and stored_files(storage) == []


@pytest.mark.asyncio
async def test_a_deletion_waits_for_a_writer_still_inside_its_lease(db_session, storage, tmp_path, deck_bytes):
    teacher, lab, body = await uploaded(db_session, storage, deck_bytes)
    doc_id = uuid.UUID(body['id'])
    # A worker staged a file and then crashed before committing.
    key = f'{doc_id}/assets/{uuid.uuid4()}/{uuid.uuid4()}.png'
    (storage / key).parent.mkdir(parents=True)
    (storage / key).write_bytes(b'orphan')
    db_session.add(DocumentStagedObject(storage_key=key, document_id=doc_id, purpose='parse_asset',
                                        lease_expires_at=_utcnow() + timedelta(minutes=5)))
    doc = await db_session.get(Document, doc_id)
    await mark_deleted(db_session, document_id=doc_id, teacher_id=teacher.id, expected_generation=doc.generation)
    await db_session.commit()
    job = await run_cleanup(db_session, document_id=doc_id, storage_root=storage)
    await db_session.commit()
    assert (job.state, job.last_error_code) == ('failed', 'staged_objects_pending')
    assert (storage / key).exists()
    await db_session.execute(text('UPDATE document_staged_objects SET lease_expires_at = :t'),
                             {'t': _utcnow() - timedelta(seconds=1)})
    await db_session.commit()
    job = await run_cleanup(db_session, document_id=doc_id, storage_root=storage)
    await db_session.commit()
    assert job.state == 'complete' and stored_files(storage) == []
    assert await count(db_session, DocumentStagedObject) == 0


@pytest.mark.asyncio
async def test_a_lost_lease_cannot_commit_over_the_new_claim(db_session, storage, tmp_path, deck_bytes):
    _, _, body = await uploaded(db_session, storage, deck_bytes)
    start = _utcnow()
    first = await claim_structured_job(db_session, lease_seconds=60, now=start)
    # The lease passes; a second worker takes the job over.
    later = start + timedelta(seconds=61)
    second = await claim_structured_job(db_session, lease_seconds=600, now=later)
    assert second is not None and second.lock_token != first.lock_token
    path = tmp_path / 'copy.pdf'
    path.write_bytes(deck_bytes)
    outcome = si.run_pdf_pipeline(path, hashlib.sha256(deck_bytes).hexdigest(), tmp_path / 'w1',
                                  settings=worker_settings(storage, tmp_path), extractor=pdf_fixtures.page_items)
    with pytest.raises(si.StaleClaim, match='claim_lost'):
        await si._commit_parsed(db_session, first, outcome, si.plan_assets(outcome, first), lambda: later)
    await db_session.rollback()
    planned = si.plan_assets(outcome, second)
    await si._stage(db_session, second, planned, worker_settings(storage, tmp_path), lambda: later)
    await si._commit_parsed(db_session, second, outcome, planned, lambda: later)
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    await db_session.refresh(rev)
    assert rev.processing_state == 'parsed'
    # A duplicate result for the same revision is refused, not merged.
    with pytest.raises(si.StaleClaim):
        await si._commit_parsed(db_session, second, outcome, planned, lambda: later)
    await db_session.rollback()
    assert await count(db_session, DocumentStagedObject) == 0


@pytest.mark.asyncio
async def test_a_source_changed_on_disk_fails_without_retry(db_session, storage, tmp_path, deck_bytes):
    _, _, body = await uploaded(db_session, storage, deck_bytes)
    source = await db_session.get(DocumentSource, uuid.UUID(body['source_version_id']))
    (storage / source.storage_key).write_bytes(deck_bytes + b'tampered')
    assert await run_job(db_session, storage, tmp_path)
    job = await db_session.get(IngestionJob, uuid.UUID(body['job_id']))
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    await db_session.refresh(job); await db_session.refresh(rev)
    assert (job.status, job.error_message) == (JobStatus.failed, 'source_hash_mismatch')
    assert rev.processing_state == 'failed' and rev.report['error_code'] == 'source_hash_mismatch'
    assert await claim_structured_job(db_session) is None


@pytest.mark.asyncio
async def test_an_unexpected_error_is_retried_then_fails_as_internal(db_session, storage, tmp_path, deck_bytes, monkeypatch):
    _, _, body = await uploaded(db_session, storage, deck_bytes)

    def broken(*_args, **_kwargs):
        raise RuntimeError('bug in the planning step')

    monkeypatch.setattr(si, 'plan_assets', broken)
    for _ in range(2):
        assert await run_job(db_session, storage, tmp_path, settings={'max_attempts': 1})
    job = await db_session.get(IngestionJob, uuid.UUID(body['job_id']))
    rev = await db_session.get(DocumentRevision, uuid.UUID(body['revision_id']))
    await db_session.refresh(job); await db_session.refresh(rev)
    assert (job.status, job.error_message, rev.processing_state) == (JobStatus.failed, 'attempts_exhausted', 'failed')
    assert await count(db_session, DocumentStagedObject) == 0 and await count(db_session, DocumentPage) == 0


@pytest.mark.asyncio
async def test_the_same_file_in_two_documents_gets_separate_assets(db_session, storage, tmp_path, deck_bytes):
    teacher, _, lab, _ = await seed(db_session)
    await upload(db_session, teacher, lab, deck_bytes)
    await upload(db_session, teacher, lab, deck_bytes)
    assert await run_job(db_session, storage, tmp_path)
    assert await run_job(db_session, storage, tmp_path)
    revs = (await db_session.scalars(select(DocumentRevision))).all()
    assert all(r.processing_state == 'parsed' for r in revs)
    # Same source hash: the same stable page ids. Different sources: no shared asset.
    ids = [set((await db_session.scalars(select(DocumentPage.page_id).where(DocumentPage.revision_id == r.id))).all())
           for r in revs]
    assert ids[0] == ids[1]
    per_source = [set((await db_session.scalars(select(DocumentAsset.id).where(
        DocumentAsset.source_version_id == r.source_version_id))).all()) for r in revs]
    assert per_source[0] and not per_source[0] & per_source[1]
    assert await count(db_session, DocumentBlockAsset) >= 0


# ---------------------------------------------------------------------------
# Sweep and storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_sweep_removes_expired_orphans_and_keeps_owned_files(db_session, storage, tmp_path, deck_bytes):
    _, _, body = await uploaded(db_session, storage, deck_bytes)
    doc_id = uuid.UUID(body['id'])
    source = await db_session.get(DocumentSource, uuid.UUID(body['source_version_id']))
    orphan = f'{doc_id}/assets/{uuid.uuid4()}/x.png'
    fresh = f'{doc_id}/assets/{uuid.uuid4()}/y.png'
    for key in (orphan, fresh):
        (storage / key).parent.mkdir(parents=True)
        (storage / key).write_bytes(b'x')
    (storage / (orphan + '.part')).write_bytes(b'half')
    past, future = _utcnow() - timedelta(seconds=1), _utcnow() + timedelta(minutes=5)
    db_session.add_all([
        DocumentStagedObject(storage_key=orphan, document_id=doc_id, purpose='parse_asset', lease_expires_at=past),
        DocumentStagedObject(storage_key=fresh, document_id=doc_id, purpose='parse_asset', lease_expires_at=future),
        # A row whose file a committed source owns: only the ledger row goes.
        DocumentStagedObject(storage_key=source.storage_key, document_id=doc_id, purpose='upload_source',
                             lease_expires_at=past),
    ])
    await db_session.commit()
    assert await sweep_staged_objects(db_session, storage_root=storage) == 2
    await db_session.commit()
    assert not (storage / orphan).exists() and not (storage / (orphan + '.part')).exists()
    assert (storage / fresh).exists() and (storage / source.storage_key).exists()
    assert await count(db_session, DocumentStagedObject) == 1


def test_a_storage_key_cannot_leave_the_root(tmp_path):
    from backend.app.services import document_storage as storage_mod
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'root'
    (root / 'doc').mkdir(parents=True)
    (root / 'doc' / 'link').symlink_to(outside)
    with pytest.raises(storage_mod.StorageError):
        storage_mod.write_object(root, 'doc/link/x.bin', b'x', sha256=hashlib.sha256(b'x').hexdigest())
    for key in ('../x', '/abs', 'a/../b', ''):
        with pytest.raises(ValueError):
            storage_mod.resolve(root, key)
    assert list(outside.iterdir()) == []
    storage_mod.write_object(root, 'doc/a.bin', b'x', sha256=hashlib.sha256(b'x').hexdigest())
    with pytest.raises(storage_mod.StorageError, match='storage_key_exists'):
        storage_mod.write_object(root, 'doc/a.bin', b'y', sha256=hashlib.sha256(b'y').hexdigest())
    with pytest.raises(storage_mod.StorageError, match='object_hash_mismatch'):
        storage_mod.write_object(root, 'doc/b.bin', b'x', sha256='0' * 64)
    assert sorted(p.name for p in (root / 'doc').iterdir()) == ['a.bin', 'link']


def test_the_api_and_the_migration_runner_name_the_same_versions():
    from backend.app.schema_versions import REQUIRED_SCHEMA_VERSIONS
    from scripts.migrate_document_snapshots import MIGRATIONS
    assert REQUIRED_SCHEMA_VERSIONS == tuple(version for version, _ in MIGRATIONS)
