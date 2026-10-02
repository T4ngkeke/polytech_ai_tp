"""M2-C on real PostgreSQL: review binding, triggers under edits, races."""
import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.models import Document, DocumentBlock, DocumentReview, DocumentRevision
from backend.app.services import document_edits as edits
from backend.app.services.document_versions import VersionConflict
from backend.tests import odp_fixtures
from backend.tests.test_document_versions_pg import m1_pg  # noqa: F401 - fixture
from backend.tests.test_structured_ingest import fake_renderer, page_texts_for, worker_settings
from backend.tests.test_structured_ingest_pg import odp_upload, seed
from backend.worker import structured_ingest as si


async def parsed_pg(factory, storage, tmp_path):
    deck = odp_fixtures.write_course_deck(tmp_path / 'deck.odp').read_bytes()
    async with factory() as db:
        teacher, lab = await seed(db)
        result = await odp_upload(db, teacher, lab, deck, storage)
        ids = {'teacher_id': teacher.id, 'doc_id': result.document.id, 'rev_id': result.revision.id}
        assert await si.process_structured_job(db, settings=worker_settings(storage, tmp_path), runner=fake_renderer(),
                                               page_count_reader=lambda _p: 3,
                                               page_texts_reader=page_texts_for(deck, tmp_path))
        block = await db.scalar(select(DocumentBlock).where(DocumentBlock.revision_id == ids['rev_id'],
                                                            DocumentBlock.content.contains('SELECT')))
        ids['block_id'] = block.block_id
        ids['generation'] = (await db.scalar(select(Document.generation).where(Document.id == ids['doc_id'])))
    return deck, ids


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / 'storage'
    root.mkdir()
    return root


@pytest.mark.asyncio
async def test_pg_review_uniqueness_is_per_revision_and_legacy_is_unchanged(m1_pg, storage, tmp_path):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    _, ids = await parsed_pg(factory, storage, tmp_path)

    def review(**kw):
        base = dict(id=uuid.uuid4(), document_id=ids['doc_id'], file_sha256='a' * 64, source_key='k' * 64,
                    source_ref={}, page_no=1, bbox=[0, 0, 1, 1], reason='r', status='pending', revision=1)
        base.update(kw)
        return DocumentReview(**base)

    async with factory() as db:
        rev = await db.get(DocumentRevision, ids['rev_id'])
        rev_id, source_id = rev.id, rev.source_version_id
        bound = dict(revision_id=rev_id, source_version_id=source_id, block_id=ids['block_id'],
                     kind='vision_description')
        db.add(review(**bound))
        await db.commit()
        db.add(review(**bound))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        # Legacy rows keep their per-document uniqueness.
        db.add(review())
        await db.commit()
        db.add(review())
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        # Half a binding is refused; so is a block of another revision.
        db.add(review(source_key='h' * 64, revision_id=rev_id))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        db.add(review(source_key='b' * 64, revision_id=rev_id, source_version_id=source_id,
                      block_id=uuid.uuid4(), kind='vision_description'))
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_pg_edit_and_reparse_under_the_snapshot_triggers(m1_pg, storage, tmp_path):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    deck, ids = await parsed_pg(factory, storage, tmp_path)
    async with factory() as db:
        rev = await edits.edit_block(db, document_id=ids['doc_id'], teacher_id=ids['teacher_id'],
                                     block_id=ids['block_id'], expected_revision_id=ids['rev_id'],
                                     expected_generation=ids['generation'], content='SELECT 3;')
        await db.commit()
        doc = await db.get(Document, ids['doc_id'])
        await db.refresh(doc)
        reparse, _ = await edits.request_reparse(db, document_id=doc.id, teacher_id=ids['teacher_id'],
                                                 expected_revision_id=rev.id, expected_generation=doc.generation)
        await db.commit()
        reparse_id = reparse.id
        assert await si.process_structured_job(db, settings=worker_settings(storage, tmp_path), runner=fake_renderer(),
                                               page_count_reader=lambda _p: 3,
                                               page_texts_reader=page_texts_for(deck, tmp_path))
        block = await db.scalar(select(DocumentBlock).where(DocumentBlock.revision_id == reparse_id,
                                                            DocumentBlock.block_id == ids['block_id']))
        assert block.content == 'SELECT 3;' and block.edited_by_teacher
        # The original draft is untouched in the database, not only in the ORM.
        raw = await db.scalar(text('SELECT content FROM document_blocks WHERE revision_id=:r AND block_id=:b'),
                              {'r': ids['rev_id'], 'b': ids['block_id']})
        assert 'SELECT\tnom' in raw


@pytest.mark.asyncio
async def test_pg_two_edits_from_the_same_view_have_one_winner(m1_pg, storage, tmp_path):
    factory = async_sessionmaker(m1_pg, expire_on_commit=False)
    _, ids = await parsed_pg(factory, storage, tmp_path)

    async def edit(text_value):
        async with factory() as db:
            try:
                await edits.edit_block(db, document_id=ids['doc_id'], teacher_id=ids['teacher_id'],
                                       block_id=ids['block_id'], expected_revision_id=ids['rev_id'],
                                       expected_generation=ids['generation'], content=text_value)
                await db.commit()
                return 'saved'
            except VersionConflict:
                await db.rollback()
                return 'conflict'

    outcomes = await asyncio.wait_for(asyncio.gather(edit('A'), edit('B')), 15)
    assert sorted(outcomes) == ['conflict', 'saved']
    async with factory() as db:
        count = await db.scalar(text("SELECT count(*) FROM document_revisions WHERE reason='teacher_edit'"))
        assert count == 1


@pytest.mark.asyncio
async def test_pg_m2c_migration_replaces_the_old_review_constraint(m1_pg):
    from scripts.migrate_document_snapshots import apply_migration
    async with m1_pg.begin() as conn:
        # Put the table back the way worker_document_reviews.sql made it.
        await conn.execute(text('DROP INDEX uq_review_legacy_source'))
        await conn.execute(text('DROP INDEX uq_review_revision_source'))
        await conn.execute(text('ALTER TABLE document_reviews ADD CONSTRAINT uq_document_review_source '
                                'UNIQUE (document_id, source_key)'))
        await conn.execute(text("DELETE FROM document_schema_migrations WHERE version='m2c_structured_review_v1'"))
        assert await apply_migration(conn) == 'applied'
        names = set((await conn.scalars(text(
            "SELECT conname FROM pg_constraint WHERE conrelid='document_reviews'::regclass"))).all())
        indexes = set((await conn.scalars(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='document_reviews' AND schemaname=current_schema()"))).all())
        assert 'uq_document_review_source' not in names and {'ck_review_binding', 'fk_review_block'} <= names
        assert {'uq_review_legacy_source', 'uq_review_revision_source'} <= indexes
        assert await apply_migration(conn) == 'already_applied'
