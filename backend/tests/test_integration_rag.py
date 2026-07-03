"""
test_integration_rag.py — [v7.1] Phase 5 ship gate (end-to-end + red lines).

Runs the WHOLE chain on live pgvector with fake model functions:
  ingest_document (parse → chunk → contextual → embed → tsv → extract)
    → hybrid_search (vector + BM25 over the tsv the worker wrote)
    → agent (route → retrieve → synthesize).

Plus the red-line regression that must hold before ship:
  * cross-tenant isolation (lab filter in SQL),
  * students never retrieve teacher-audience material (audience filter in SQL),
  * no corrigé is ingested — there is no `solution` column / field anywhere.
"""

import uuid

import pytest

from backend.app.agent.graph import build_agent
from backend.app.models import (
    Audience, Class, DocChunk, DocType, EMBEDDING_DIM, Exercise, Lab, UserRole,
)
from backend.app.services.document_service import create_document
from backend.worker.ingest import ingest_document
from backend.tests.conftest import make_user


def _unit(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


async def _fixed_embed(texts):
    # Same vector for every text → vector recall always matches; BM25 does the
    # discriminating via the tsv the worker writes.
    return [_unit(1) for _ in texts]


async def _seed_lab(session, name="Lab 1"):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name=name)
    session.add(lab)
    await session.flush()
    return teacher, cls, lab


@pytest.mark.asyncio
async def test_e2e_ingest_then_agent_retrieves_context(pg_session, tmp_path):
    teacher, cls, lab = await _seed_lab(pg_session)
    student = make_user(role=UserRole.student)
    pg_session.add(student)
    # [v7.3] strict split: concept RAG is fed by CM material only (a TD would
    # produce Exercises, never chunks).
    body = "Parity and error detection in binary coding.\n"
    doc = await create_document(
        pg_session, class_id=cls.id, lab_id=lab.id, filename="cm.txt",
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
        doc_type=DocType.CM, audience=Audience.student,
    )

    # Worker ingests: writes DocChunks (with tsv via to_tsvector).
    await ingest_document(pg_session, doc.id, embed_fn=_fixed_embed)

    # Agent answers a concept question end-to-end.
    agent = build_agent(pg_session, embed_fn=_fixed_embed)
    result = await agent.ainvoke({
        "message": "Explain parity in binary coding",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "rag"
    system = result["messages_payload"][0]["content"]
    assert "Parity and error detection" in system
    assert result["citations"], "expected at least one citation"


@pytest.mark.asyncio
async def test_ingest_builds_french_tsv_matching_inflected_queries(pg_session, tmp_path):
    """[v7.3] The worker writes the tsvector with the document's language config
    (default fr), so a singular query matches the plural document via stemming.
    Ingest and query sides sharing the config is what keeps BM25 alive."""
    from backend.app.services.retrieval_service import bm25_search

    teacher, cls, lab = await _seed_lab(pg_session)
    body = "Les fonctions recursives sont puissantes et elegantes en pratique.\n"
    doc = await create_document(
        pg_session, class_id=cls.id, lab_id=lab.id, filename="cm.txt",
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
        doc_type=DocType.CM, audience=Audience.student,
    )
    await ingest_document(pg_session, doc.id, embed_fn=_fixed_embed)

    hits = await bm25_search(
        pg_session, "fonction recursive", lab.id,
        audience=Audience.student, language="fr",
    )
    assert len(hits) == 1
    assert "fonctions recursives" in hits[0].content


@pytest.mark.asyncio
async def test_redline_cross_tenant_and_audience_and_no_solution(pg_session, tmp_path):
    teacher, cls, lab_a = await _seed_lab(pg_session, "Lab A")
    student = make_user(role=UserRole.student)
    pg_session.add(student)

    # Lab A: a student-visible chunk.
    doc_a = await create_document(
        pg_session, class_id=cls.id, lab_id=lab_a.id, filename="a.txt",
        content=b"Exercice 1\nthreads in lab A\n", uploaded_by=teacher.id,
        storage_root=tmp_path, doc_type=DocType.TD, audience=Audience.student,
    )
    await ingest_document(pg_session, doc_a.id, embed_fn=_fixed_embed)

    # A second lab with its own chunk (cross-tenant bait).
    lab_b = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab B")
    pg_session.add(lab_b)
    await pg_session.flush()
    doc_b = await create_document(
        pg_session, class_id=cls.id, lab_id=lab_b.id, filename="b.txt",
        content=b"threads in lab B\n", uploaded_by=teacher.id,
        storage_root=tmp_path, doc_type=DocType.CM, audience=Audience.student,
    )
    await ingest_document(pg_session, doc_b.id, embed_fn=_fixed_embed)

    # A teacher-only chunk inside lab A (audience bait).
    pg_session.add(DocChunk(
        id=uuid.uuid4(), document_id=doc_a.id, class_id=cls.id, lab_id=lab_a.id,
        doc_type=DocType.TD, audience=Audience.teacher, chunk_index=99,
        content="teacher-only threads key", embedding=_unit(1), page_no=1,
    ))
    await pg_session.commit()

    agent = build_agent(pg_session, embed_fn=_fixed_embed)
    result = await agent.ainvoke({
        "message": "Tell me about threads",
        "class_id": cls.id, "lab_id": lab_a.id, "user_id": student.id, "history": [],
    })

    context = " ".join(result.get("context_blocks", []))
    # Red line 1: cross-tenant — lab B never appears.
    assert "lab B" not in context
    # Red line 2: audience — the teacher-only chunk never appears for a student.
    assert "teacher-only" not in context

    # Red line 3: no corrigé is ingested — the column does not exist, and stored
    # exercises expose only statements.
    assert "solution" not in Exercise.__table__.columns
    exercises = (await pg_session.execute(
        Exercise.__table__.select().where(Exercise.lab_id == lab_a.id)
    )).all()
    assert exercises and all(not hasattr(e, "solution") for e in exercises)
