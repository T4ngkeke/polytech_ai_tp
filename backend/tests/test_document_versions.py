"""M1 synthetic acceptance: no parser, model, production DB or real course files."""
import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from backend.app.config import settings
from backend.app.models import (
    Class, ClassStudent, Lab, Session, Document, DocumentSource, DocumentRevision,
    DocumentPage, DocumentBlock, DocumentAsset, DocChunk, DocumentCleanupJob,
    EMBEDDING_DIM, UserRole,
)
from backend.app.services import document_versions as versions
from backend.app.services.document_access import readable_revision, readable_asset, DocumentNotFound
from backend.app.services.document_cleanup import mark_deleted, run_cleanup
from backend.app.services.retrieval_service import _scope
from backend.tests.conftest import make_user, make_client


@pytest.fixture(autouse=True)
def enable_structured(monkeypatch):
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)


async def seed(db, tmp_path):
    teacher, student, outsider = make_user(role=UserRole.teacher), make_user(), make_user()
    db.add_all([teacher, student, outsider]); await db.flush()
    cls = Class(id=uuid.uuid4(), name='M1 synthetic', teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db.add(cls); await db.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name='Lab')
    db.add(lab); await db.flush()
    db.add(ClassStudent(class_id=cls.id, student_id=student.id))
    chat = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=student.id)
    other_chat = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=outsider.id)
    db.add_all([chat, other_chat]); await db.flush()
    doc = await versions.new_document(db, teacher_id=teacher.id, class_id=cls.id,
                                     lab_id=lab.id, filename='synthetic.odp')
    await db.commit()
    return doc, teacher, student, outsider, chat, other_chat


async def draft(db, doc, teacher, tmp_path, label='one'):
    key = f'{doc.id}/{label}.odp'
    file = tmp_path / key; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(label.encode())
    rev = await versions.register_source(db, document_id=doc.id, teacher_id=teacher.id,
        expected_generation=doc.generation, sha256=hashlib.sha256(label.encode()).hexdigest(),
        format='odp', original_filename='synthetic.odp', key=key)
    await db.commit()
    return rev


async def content(db, doc, rev, tmp_path, body='if (!p) { return 1; }'):
    key = f'{doc.id}/{rev.id}.png'; (tmp_path/key).write_bytes(b'synthetic-preview')
    asset = DocumentAsset(id=uuid.uuid4(), source_version_id=rev.source_version_id, storage_key=key,
        sha256=hashlib.sha256(b'synthetic-preview').hexdigest(), mime='image/png',
        kind='page_preview', access_class='student_visible')
    db.add(asset); await db.flush()
    page = DocumentPage(revision_id=rev.id, source_version_id=rev.source_version_id,
        page_id=uuid.uuid4(), order_index=0, source_slide_no=1, preview_page_no=1, preview_asset_id=asset.id)
    db.add(page); await db.flush()
    block = DocumentBlock(revision_id=rev.id, source_version_id=rev.source_version_id,
        block_id=uuid.uuid4(), page_id=page.page_id, order_index=0, kind='code', content=body,
        source_text=body, source_ref={'native_object_ids':['test-object']},
        source_completeness='complete', index_state='searchable')
    db.add(block); await db.flush()
    from backend.app.services.document_service import _tsv_value
    chunk = DocChunk(document_id=doc.id, class_id=doc.class_id, lab_id=doc.lab_id,
        revision_id=rev.id, block_id=block.block_id, chunk_index=0, content=body,
        embedding=[0.0]*(EMBEDDING_DIM-1)+[1.0], tsv=_tsv_value(db, body) if db.bind.dialect.name=='postgresql' else body,
        part_index=0, part_count=1, content_start=0, content_end=len(body), page_no=1)
    db.add(chunk); await db.flush()
    rev.processing_state='ready'; rev.index_ready=True
    rev.report={'preview_mapping_valid':True, 'source_coverage_complete':True, 'issues':[]}
    await db.commit()
    return block, asset, chunk


async def published(db, tmp_path):
    doc, teacher, student, outsider, chat, other = await seed(db, tmp_path)
    rev = await draft(db, doc, teacher, tmp_path)
    block, asset, chunk = await content(db, doc, rev, tmp_path)
    await versions.publish_revision(db, document_id=doc.id, revision_id=rev.id,
        teacher_id=teacher.id, expected_generation=doc.generation)
    await db.commit()
    return doc, teacher, student, outsider, chat, other, rev, block, asset, chunk


@pytest.mark.asyncio
async def test_source_idempotency_and_failed_replacement_preserves_old(db_session, tmp_path):
    db = db_session
    doc, teacher, student, _, chat, _, old, *_ = await published(db, tmp_path)
    generation = doc.generation
    same = await draft(db, doc, teacher, tmp_path)
    assert same.id == old.id and doc.generation == generation
    new = await draft(db, doc, teacher, tmp_path, 'two')
    await versions.set_processing_result(db, document_id=doc.id, revision_id=new.id,
        expected_generation=doc.generation, state='failed', report={'issues':['unreadable']})
    await db.commit()
    assert doc.current_revision_id == old.id
    assert (await readable_revision(db, document_id=doc.id, user_id=student.id, session_id=chat.id)).id == old.id
    assert not list((await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id==new.id))).all())


@pytest.mark.asyncio
async def test_ready_auto_publish_and_partial_acknowledgement(db_session, tmp_path):
    db=db_session
    doc, teacher, *_ = await seed(db, tmp_path)
    first = await draft(db, doc, teacher, tmp_path)
    await content(db, doc, first, tmp_path)
    await versions.set_processing_result(db, document_id=doc.id, revision_id=first.id,
        expected_generation=doc.generation, state='ready', report=first.report, index_ready=True)
    await db.commit()
    assert doc.current_revision_id == first.id
    second = await draft(db, doc, teacher, tmp_path, 'two'); await content(db, doc, second, tmp_path)
    second.processing_state='partial'; second.report={**second.report, 'issues':['unreadable diagram']}
    await db.commit()
    ids=(doc.id,teacher.id,second.id)
    with pytest.raises(versions.VersionConflict, match='acknowledge'):
        await versions.publish_revision(db, document_id=doc.id, revision_id=second.id,
            teacher_id=teacher.id, expected_generation=doc.generation)
    await db.rollback()
    doc=await db.get(Document,ids[0]); second=await db.get(DocumentRevision,ids[2])
    await versions.publish_revision(db, document_id=doc.id, revision_id=second.id,
        teacher_id=ids[1], expected_generation=doc.generation, acknowledge_issues=True)
    await db.commit()
    assert doc.current_revision_id==second.id
    await db.refresh(first); assert first.publication_state=='superseded'


@pytest.mark.asyncio
async def test_snapshot_edit_and_old_citation_survives(db_session, tmp_path):
    db=db_session
    doc, teacher, student, _, chat, _, old, block, asset, _ = await published(db,tmp_path)
    clone=await versions.clone_revision(db, document_id=doc.id, teacher_id=teacher.id,
        expected_generation=doc.generation, expected_revision_id=old.id)
    copied=await db.get(DocumentBlock,(clone.id,block.block_id))
    copied.content='teacher corrected'; copied.content_description=None
    await db.commit()
    assert (await db.get(DocumentBlock,(old.id,block.block_id))).content=='if (!p) { return 1; }'
    assert clone.index_ready is False
    assert await readable_asset(db, asset_id=asset.id, document_id=doc.id,
        revision_id=old.id, user_id=student.id, session_id=chat.id)
    with pytest.raises(DocumentNotFound):
        await readable_revision(db, document_id=doc.id, revision_id=clone.id,
                                user_id=student.id, session_id=chat.id)


@pytest.mark.asyncio
async def test_archive_restore_membership_and_asset_scope(db_session,tmp_path):
    db=db_session
    doc, teacher, student, outsider, chat, other, rev, _, asset, _=await published(db,tmp_path)
    with pytest.raises(DocumentNotFound):
        await readable_asset(db,asset_id=asset.id,document_id=doc.id,revision_id=rev.id,
            user_id=outsider.id,session_id=other.id)
    await versions.set_archived(db,document_id=doc.id,teacher_id=teacher.id,
                               expected_generation=doc.generation,archived=True)
    await db.commit()
    with pytest.raises(DocumentNotFound):
        await readable_revision(db,document_id=doc.id,user_id=student.id,session_id=chat.id)
    assert await readable_revision(db,document_id=doc.id,revision_id=rev.id,user_id=teacher.id,teacher=True)
    assert not list((await db.scalars(_scope(select(DocChunk),doc.lab_id,doc.class_id))).all())
    await versions.set_archived(db,document_id=doc.id,teacher_id=teacher.id,
                               expected_generation=doc.generation,archived=False)
    await db.commit()
    assert doc.current_revision_id==rev.id
    membership=await db.get(ClassStudent,(doc.class_id,student.id)); await db.delete(membership); await db.commit()
    with pytest.raises(DocumentNotFound):
        await readable_revision(db,document_id=doc.id,user_id=student.id,session_id=chat.id)


@pytest.mark.asyncio
async def test_cleanup_revokes_immediately_retries_and_keeps_minimal_tombstone(db_session,tmp_path,monkeypatch):
    db=db_session
    doc,teacher,student,_,chat,_,rev,_,asset,_=await published(db,tmp_path)
    await mark_deleted(db,document_id=doc.id,teacher_id=teacher.id,expected_generation=doc.generation)
    await db.commit()
    with pytest.raises(DocumentNotFound):
        await readable_asset(db,asset_id=asset.id,document_id=doc.id,revision_id=rev.id,user_id=student.id,session_id=chat.id)
    original=Path.unlink
    with monkeypatch.context() as m:
        def fail(self,**kwargs): raise PermissionError('temporary')
        m.setattr(Path,'unlink',fail)
        job=await run_cleanup(db,document_id=doc.id,storage_root=tmp_path); await db.commit()
        assert job.state=='failed' and job.remaining_storage_keys
    job=await run_cleanup(db,document_id=doc.id,storage_root=tmp_path); await db.commit()
    assert job.state=='complete' and not job.remaining_storage_keys
    assert doc.filename==doc.storage_path==doc.content_hash==''
    from backend.app.services.document_service import list_lab_documents
    assert await list_lab_documents(db,doc.lab_id)==[]
    assert not list((await db.scalars(select(DocumentRevision))).all())
    assert not list((await db.scalars(select(DocChunk))).all())
    assert not list((await db.scalars(select(DocumentSource))).all())
    assert (await run_cleanup(db,document_id=doc.id,storage_root=tmp_path)).attempts==2


@pytest.mark.asyncio
async def test_publish_rejects_wrong_slice_and_rollback_preserves_generation(db_session,tmp_path):
    db=db_session; doc,teacher,*_=await seed(db,tmp_path)
    rev=await draft(db,doc,teacher,tmp_path); _,_,chunk=await content(db,doc,rev,tmp_path)
    chunk.content='invented'; await db.commit()
    ids=(doc.id,teacher.id,rev.id); gen=doc.generation
    with pytest.raises(versions.VersionConflict,match='invalid_index'):
        await versions.publish_revision(db,document_id=doc.id,revision_id=rev.id,
                                       teacher_id=teacher.id,expected_generation=gen)
    await db.rollback(); doc=await db.get(Document,ids[0])
    assert doc.generation==gen and doc.current_revision_id is None


@pytest.mark.asyncio
async def test_published_content_and_source_are_immutable(db_session,tmp_path):
    db=db_session; doc,teacher,_,_,_,_,rev,block,_,_=await published(db,tmp_path)
    block.content='silently changed'
    with pytest.raises(ValueError,match='immutable_revision'): await db.flush()
    await db.rollback()
    source=(await db.scalars(select(DocumentSource))).first(); source.storage_key='other'
    with pytest.raises(ValueError,match='immutable_source'): await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_feature_flag_off_disables_new_structured_documents(db_session,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'STRUCTURED_INGEST_ENABLED',False)
    with pytest.raises(versions.VersionConflict,match='disabled'):
        await versions.new_document(db_session,teacher_id=uuid.uuid4(),class_id=uuid.uuid4(),lab_id=None,filename='x')


@pytest.mark.asyncio
async def test_teacher_routes_authorization_conflict_and_delete(db_session,tmp_path):
    db=db_session; doc,teacher,student,_,_,_,rev,*_=await published(db,tmp_path)
    async with await make_client(db,teacher) as client:
        response=await client.get(f'/api/teacher/documents/{doc.id}/revisions')
        assert response.status_code==200 and response.json()['current_revision_id']==str(rev.id)
        response=await client.post(f'/api/teacher/documents/{doc.id}/archive',json={'expected_generation':0})
        assert response.status_code==409
    await db.refresh(doc)
    await db.refresh(student)
    await db.refresh(teacher)
    async with await make_client(db,student) as client:
        assert (await client.get(f'/api/teacher/documents/{doc.id}/revisions')).status_code==403
    async with await make_client(db,teacher) as client:
        response=await client.delete(f'/api/teacher/documents/{doc.id}?expected_generation={doc.generation}')
        assert response.status_code==202


@pytest.mark.asyncio
async def test_cleanup_rejects_symlink_escape(db_session,tmp_path):
    db=db_session
    doc,teacher,*_=await seed(db,tmp_path)
    await draft(db,doc,teacher,tmp_path)
    outside=tmp_path.parent / ('m1-outside-'+uuid.uuid4().hex)
    outside.write_bytes(b'keep')
    try:
        source=(await db.scalars(select(DocumentSource))).first()
        path=tmp_path/source.storage_key; path.unlink(); path.symlink_to(outside)
        await mark_deleted(db,document_id=doc.id,teacher_id=teacher.id,expected_generation=doc.generation)
        await db.commit()
        job=await run_cleanup(db,document_id=doc.id,storage_root=tmp_path); await db.commit()
        assert job.state=='failed' and outside.read_bytes()==b'keep'
        path.unlink()
        job=await run_cleanup(db,document_id=doc.id,storage_root=tmp_path)
        assert job.state=='complete'
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cross_document_asset_and_cross_lab_read_denied(db_session,tmp_path):
    db=db_session
    doc,teacher,student,_,chat,_,rev,_,asset,_=await published(db,tmp_path)
    other,teacher2,_,_,_,_,rev2,_,asset2,_=await published(db,tmp_path)
    with pytest.raises(DocumentNotFound):
        await readable_asset(db,asset_id=asset2.id,document_id=doc.id,revision_id=rev.id,
            user_id=student.id,session_id=chat.id)
    # Even membership in the class is not access to every lab's private material.
    lab=Lab(id=uuid.uuid4(),class_id=doc.class_id,name='another lab');db.add(lab);await db.flush()
    other_chat=Session(id=uuid.uuid4(),user_id=student.id,lab_id=lab.id);db.add(other_chat);await db.commit()
    with pytest.raises(DocumentNotFound):
        await readable_revision(db,document_id=doc.id,user_id=student.id,session_id=other_chat.id)
    async with await make_client(db,teacher2) as client:
        assert (await client.get(f'/api/teacher/documents/{doc.id}/revisions')).status_code==404


@pytest.mark.asyncio
async def test_shared_document_read_and_unlinked_asset_denied(db_session,tmp_path):
    db=db_session
    doc,teacher,student,_,chat,_,rev,_,asset,_=await published(db,tmp_path)
    doc.lab_id=None;await db.commit()
    assert await readable_revision(db,document_id=doc.id,user_id=student.id,session_id=chat.id)
    unlinked=DocumentAsset(id=uuid.uuid4(),source_version_id=rev.source_version_id,
        storage_key=f'{doc.id}/unlinked.png',sha256='f'*64,mime='image/png',kind='embedded_image',access_class='student_visible')
    db.add(unlinked);await db.commit()
    with pytest.raises(DocumentNotFound):
        await readable_asset(db,asset_id=unlinked.id,document_id=doc.id,revision_id=rev.id,
                             user_id=student.id,session_id=chat.id)
