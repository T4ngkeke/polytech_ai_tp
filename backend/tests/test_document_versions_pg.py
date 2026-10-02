"""Real M1 migration/locking/FK tests. Each test owns a random isolated schema.

M1_TEST_PG_URL must explicitly name a disposable *_test database. Connection or
DDL failures FAIL rather than silently skip; no implicit application URL.
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import DBAPIError, IntegrityError
from backend.app.config import settings
from backend.app.database import Base
from backend.app.models import Document, DocumentRevision, DocumentBlock, DocumentPage, DocChunk, DocumentSource
from backend.app.services import document_versions as versions
from backend.app.services.document_cleanup import mark_deleted, run_cleanup
from backend.app.services.retrieval_service import bm25_search
from backend.tests.test_document_versions import seed, draft, content, published
from scripts.migrate_document_snapshots import apply_migration, SQL_PATH


@pytest_asyncio.fixture
async def m1_pg(monkeypatch):
    url=os.environ.get('M1_TEST_PG_URL')
    if not url: pytest.skip('Set explicit M1_TEST_PG_URL to isolated *_test DB')
    assert make_url(url).database.endswith('_test')
    monkeypatch.setattr(settings,'STRUCTURED_INGEST_ENABLED',True)
    schema='m1_'+uuid.uuid4().hex
    admin=create_async_engine(url,poolclass=NullPool)
    async with admin.begin() as conn: await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine=create_async_engine(url,poolclass=NullPool,connect_args={'server_settings':{'search_path':f'{schema},public'}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public'))
            await conn.run_sync(Base.metadata.create_all)
            await apply_migration(conn)
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn: await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_pg_concurrent_publish_has_one_winner(m1_pg,tmp_path):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        doc,teacher,*_=await seed(db,tmp_path)
        rev=await draft(db,doc,teacher,tmp_path); await content(db,doc,rev,tmp_path)
        ids=(doc.id,teacher.id,rev.id,doc.generation)
    async def publish():
        async with factory() as db:
            try:
                await versions.publish_revision(db,document_id=ids[0],teacher_id=ids[1],revision_id=ids[2],expected_generation=ids[3])
                await db.commit(); return 'published'
            except versions.VersionConflict:
                await db.rollback(); return 'conflict'
    assert sorted(await asyncio.wait_for(asyncio.gather(publish(),publish()),15))==['conflict','published']


@pytest.mark.asyncio
async def test_pg_delete_and_publish_cannot_resurrect(m1_pg,tmp_path):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        doc,teacher,*_=await seed(db,tmp_path)
        rev=await draft(db,doc,teacher,tmp_path); await content(db,doc,rev,tmp_path)
        ids=(doc.id,teacher.id,rev.id,doc.generation)
    async def act(deleting):
        async with factory() as db:
            try:
                if deleting:
                    await mark_deleted(db,document_id=ids[0],teacher_id=ids[1],expected_generation=ids[3])
                else:
                    await versions.publish_revision(db,document_id=ids[0],teacher_id=ids[1],revision_id=ids[2],expected_generation=ids[3])
                await db.commit(); return 'won'
            except versions.VersionConflict:
                await db.rollback(); return 'conflict'
    assert sorted(await asyncio.wait_for(asyncio.gather(act(True),act(False)),15))==['conflict','won']
    async with factory() as db:
        doc=await db.get(Document,ids[0])
        if doc.lifecycle!='deleted':
            await mark_deleted(db,document_id=doc.id,teacher_id=ids[1],expected_generation=doc.generation); await db.commit()
        generation=doc.generation
        with pytest.raises(versions.VersionConflict):
            await versions.set_processing_result(db,document_id=ids[0],revision_id=ids[2],expected_generation=generation,
                state='ready',report={'preview_mapping_valid':True},index_ready=True)
        await db.rollback()
        job=await run_cleanup(db,document_id=ids[0],storage_root=tmp_path); await db.commit()
        assert job.state=='complete'


@pytest.mark.asyncio
async def test_pg_constraints_and_published_rows_protect_direct_sql(m1_pg,tmp_path):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        doc,teacher,_,_,_,_,rev,block,_,_=await published(db,tmp_path)
        ids=(doc.id,teacher.id,rev.id,block.block_id)
        with pytest.raises(DBAPIError,match='immutable_revision'):
            await db.execute(update(DocumentBlock).where(DocumentBlock.revision_id==rev.id).values(content='tamper'))
        await db.rollback()
        other,teacher2,*_=await seed(db,tmp_path)
        rev2=await draft(db,other,teacher2,tmp_path)
        # FK rejects a current revision belonging to a different document.
        with pytest.raises(IntegrityError):
            await db.execute(update(Document).where(Document.id==ids[0]).values(current_revision_id=rev2.id))
        await db.rollback()


@pytest.mark.asyncio
async def test_pg_search_only_current_and_cleanup_all_revisions(m1_pg,tmp_path):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        doc,teacher,student,_,chat,_,old,*_=await published(db,tmp_path)
        second=await draft(db,doc,teacher,tmp_path,'two'); await content(db,doc,second,tmp_path,body='replacementword')
        assert not await bm25_search(db,'replacementword',doc.lab_id,class_id=doc.class_id)
        await versions.publish_revision(db,document_id=doc.id,teacher_id=teacher.id,revision_id=second.id,expected_generation=doc.generation)
        await db.commit()
        assert not await bm25_search(db,'return',doc.lab_id,class_id=doc.class_id)
        assert await bm25_search(db,'replacementword',doc.lab_id,class_id=doc.class_id)
        from backend.app.services.document_access import readable_revision
        assert (await readable_revision(db,document_id=doc.id,revision_id=old.id,
            user_id=student.id,session_id=chat.id)).id==old.id
        await mark_deleted(db,document_id=doc.id,teacher_id=teacher.id,expected_generation=doc.generation); await db.commit()
        assert not await bm25_search(db,'replacementword',doc.lab_id,class_id=doc.class_id)
        job=await run_cleanup(db,document_id=doc.id,storage_root=tmp_path); await db.commit()
        assert job.state=='complete'


@pytest.mark.asyncio
async def test_pg_migration_repeatable_and_transactional(m1_pg):
    async with m1_pg.begin() as conn:
        assert await apply_migration(conn)=='already_applied'
        # Direct SQL itself must also be repeatable, not only the migration ledger.
        for stmt in SQL_PATH.read_text().split('-- statement')[1:]:
            if stmt.strip(): await conn.execute(text(stmt))
    # A real legacy schema: clone metadata, remove the new tables/columns/constraints.
    legacy=MetaData()
    new_names={t for t in Base.metadata.tables if t.startswith('document_') and t not in ('document_reviews',)}
    for table in Base.metadata.sorted_tables:
        if table.name not in new_names: table.to_metadata(legacy)
    removed={'documents':{'lifecycle','generation','current_revision_id','structured','deleted_at'},
             'doc_chunks':{'revision_id','block_id','part_index','part_count','content_start','content_end','token_estimate','chunker_version'},
             # M2-B columns: a real pre-M1 database has none of them either.
             'ingestion_jobs':{'task_kind','source_version_id','revision_id','lock_token','claimed_generation','lease_expires_at'},
             # M2-C columns.
             'document_reviews':{'source_version_id','revision_id','block_id','kind'}}
    for name,columns in removed.items():
        table=legacy.tables[name]
        for constraint in list(table.constraints):
            if constraint.name in {'ck_document_lifecycle','ck_document_generation','fk_document_current_revision','fk_chunk_revision','fk_chunk_block','ck_chunk_range',
                                   'fk_job_source','fk_job_revision','uq_job_revision','ck_job_binding',
                                   'fk_review_revision','fk_review_source','fk_review_block','ck_review_binding'}:
                table.constraints.remove(constraint)
                for element in getattr(constraint, "elements", []):
                    table.foreign_keys.discard(element)
                    element.parent.foreign_keys.discard(element)
        for index in list(table.indexes):
            # The partial legacy review index names revision_id only in its WHERE.
            if any(c.name in columns for c in index.columns) or index.name == 'uq_review_legacy_source':
                table.indexes.remove(index)
        for col in columns: table._columns.remove(table.c[col])
    # Use a second schema within the isolated DB, with no fallback to the first.
    async with m1_pg.begin() as conn:
        await conn.execute(text('CREATE SCHEMA legacy_m1'))
    try:
        async with m1_pg.begin() as conn:
            await conn.execute(text('SET LOCAL search_path TO legacy_m1, public'))
            await conn.run_sync(legacy.create_all)
            teacher_id, class_id, doc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await conn.execute(legacy.tables['users'].insert().values(id=teacher_id,
                username='legacy', hashed_password='x', role='teacher'))
            await conn.execute(legacy.tables['classes'].insert().values(id=class_id,
                teacher_id=teacher_id, name='legacy', invite_code='m1test'))
            await conn.execute(legacy.tables['documents'].insert().values(id=doc_id,
                class_id=class_id, filename='legacy.pdf', storage_path='/legacy.pdf',
                content_hash='a'*64, uploaded_by=teacher_id))
        async with m1_pg.connect() as conn:
            tx=await conn.begin(); await conn.execute(text('SET LOCAL search_path TO legacy_m1, public'))
            assert await apply_migration(conn)=='applied'
            await tx.rollback()
        async with m1_pg.begin() as conn:
            await conn.execute(text('SET LOCAL search_path TO legacy_m1, public'))
            assert await conn.scalar(text("SELECT to_regclass('legacy_m1.document_revisions')")) is None
            assert await apply_migration(conn)=='applied'
            assert await conn.scalar(text('SELECT count(*) FROM users'))==1
            row=(await conn.execute(text('SELECT filename,lifecycle,generation,structured FROM documents'))).one()
            assert tuple(row)==('legacy.pdf','active',0,False)
            assert await apply_migration(conn)=='already_applied'
    finally:
        async with m1_pg.begin() as conn: await conn.execute(text('DROP SCHEMA legacy_m1 CASCADE'))


@pytest.mark.asyncio
async def test_pg_block_page_fk_and_chunk_revision_identity(m1_pg,tmp_path):
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        doc,teacher,_,_,_,_,old,oldblock,_,chunk=await published(db,tmp_path)
        chunk_id,old_id=chunk.id,old.id
        new=await draft(db,doc,teacher,tmp_path,'two')
        new_id,source_id=new.id,new.source_version_id
        with pytest.raises(IntegrityError):
            # A valid page ID from another revision is still invalid here.
            await db.execute(DocumentBlock.__table__.insert().values(revision_id=new_id,
                block_id=uuid.uuid4(),source_version_id=source_id,page_id=oldblock.page_id,
                order_index=0,kind='text',content='wrong page'))
        await db.rollback()
        with pytest.raises(DBAPIError,match='immutable_revision_identity'):
            await db.execute(update(DocChunk).where(DocChunk.id==chunk_id).values(revision_id=None,block_id=None))
        await db.rollback()
        with pytest.raises(DBAPIError,match='immutable_revision'):
            await db.execute(DocumentBlock.__table__.insert().values(revision_id=old_id,
                block_id=uuid.uuid4(),source_version_id=source_id,page_id=uuid.uuid4(),
                order_index=99,kind='text',content='late writer'))
        await db.rollback()


@pytest.mark.asyncio
async def test_pg_archive_racing_publish_and_late_asset_after_delete(m1_pg,tmp_path):
    from backend.app.models import DocumentAsset
    factory=async_sessionmaker(m1_pg,expire_on_commit=False)
    async with factory() as db:
        doc,teacher,*_=await seed(db,tmp_path)
        rev=await draft(db,doc,teacher,tmp_path);await content(db,doc,rev,tmp_path)
        doc_id,teacher_id,rev_id,gen,source_id=doc.id,teacher.id,rev.id,doc.generation,rev.source_version_id
    async def attempt(archive):
        async with factory() as db:
            try:
                if archive:
                    await versions.set_archived(db,document_id=doc_id,teacher_id=teacher_id,expected_generation=gen,archived=True)
                else:
                    await versions.publish_revision(db,document_id=doc_id,teacher_id=teacher_id,revision_id=rev_id,expected_generation=gen)
                await db.commit();return 'won'
            except versions.VersionConflict:
                await db.rollback();return 'conflict'
    assert sorted(await asyncio.wait_for(asyncio.gather(attempt(True),attempt(False)),15))==['conflict','won']
    async with factory() as db:
        doc=await db.get(Document,doc_id)
        await mark_deleted(db,document_id=doc_id,teacher_id=teacher_id,expected_generation=doc.generation);await db.commit()
        with pytest.raises(DBAPIError,match='source_unavailable'):
            await db.execute(DocumentAsset.__table__.insert().values(id=uuid.uuid4(),source_version_id=source_id,
                storage_key=f'{doc_id}/late.png',sha256='f'*64,mime='image/png',kind='embedded_image',access_class='student_visible'))
        await db.rollback()
