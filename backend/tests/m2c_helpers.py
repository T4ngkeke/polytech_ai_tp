"""Shared set-up for the M2-C tests: a parsed ODP draft, and a synthetic index.

fake_index stands in for M3. It writes one chunk per searchable block, exactly
equal to the block content, so the unchanged M1 publication check can pass.
It is test scaffolding; nothing in the application writes chunks this way.
"""
import uuid

from sqlalchemy import select

from backend.app.models import (
    ClassStudent, DocChunk, Document, DocumentBlock, DocumentRevision, EMBEDDING_DIM, IngestionJob,
    Session, UserRole,
)
from backend.app.services import document_versions as versions
from backend.tests import odp_fixtures
from backend.tests.conftest import make_user
from backend.tests.test_structured_ingest import run_job, seed, upload


async def parsed(db, storage, tmp_path, content):
    teacher, outsider, lab, other_lab = await seed(db)
    body = (await upload(db, teacher, lab, content)).json()
    assert await run_job(db, storage, tmp_path, content)
    ids = {'teacher_id': teacher.id, 'outsider_id': outsider.id, 'lab_id': lab.id, 'other_lab_id': other_lab.id,
           'class_id': lab.class_id, 'doc_id': uuid.UUID(body['id']), 'rev_id': uuid.UUID(body['revision_id'])}
    return teacher, outsider, lab, ids


async def generation(db, doc_id):
    doc = await db.get(Document, doc_id)
    await db.refresh(doc)
    return doc.generation


async def fake_index(db, rev_id):
    rev = await db.get(DocumentRevision, rev_id)
    await db.refresh(rev)
    blocks = (await db.scalars(select(DocumentBlock).where(DocumentBlock.revision_id == rev_id))).all()
    doc = await db.get(Document, rev.document_id)
    for number, block in enumerate(b for b in blocks if b.index_state == 'searchable' and b.content):
        db.add(DocChunk(document_id=doc.id, class_id=doc.class_id, lab_id=doc.lab_id, revision_id=rev.id,
                        block_id=block.block_id, chunk_index=number, content=block.content,
                        embedding=[0.0] * (EMBEDDING_DIM - 1) + [1.0], tsv=block.content,
                        part_index=0, part_count=1, content_start=0, content_end=len(block.content)))
    rev.processing_state, rev.index_ready = 'partial', True
    await db.commit()


async def published(db, storage, tmp_path, slides=3):
    content = odp_fixtures.write_minimal(tmp_path / f'min-{uuid.uuid4().hex}.odp', slides=slides).read_bytes()
    teacher, outsider, lab, ids = await parsed(db, storage, tmp_path, content)
    await fake_index(db, ids['rev_id'])
    await versions.publish_revision(db, document_id=ids['doc_id'], revision_id=ids['rev_id'],
                                    teacher_id=ids['teacher_id'], expected_generation=await generation(db, ids['doc_id']),
                                    acknowledge_issues=True)
    await db.commit()
    student = make_user(role=UserRole.student)
    stranger = make_user(role=UserRole.student)
    db.add_all([student, stranger])
    await db.flush()
    db.add(ClassStudent(class_id=ids['class_id'], student_id=student.id))
    chat = Session(id=uuid.uuid4(), lab_id=ids['lab_id'], user_id=student.id)
    stranger_chat = Session(id=uuid.uuid4(), lab_id=ids['lab_id'], user_id=stranger.id)
    db.add_all([chat, stranger_chat])
    await db.commit()
    ids.update(student_id=student.id, chat_id=chat.id, stranger_chat_id=stranger_chat.id)
    return teacher, student, stranger, ids


async def job_for(db, rev_id):
    return await db.scalar(select(IngestionJob).where(IngestionJob.revision_id == rev_id))
