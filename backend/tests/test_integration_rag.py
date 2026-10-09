"""
test_integration_rag.py — end-to-end ship gate.

Runs the WHOLE chain on live pgvector with fake model functions:
  ingest_document (parse → chunk → contextual → embed → tsv)
    → hybrid_search (vector + BM25 over the tsv the worker wrote)
    → agent (route → retrieve → synthesize).

Plus the red line that must hold before ship: cross-tenant isolation, enforced
in the SQL WHERE clause rather than in a prompt.

The audience red line is gone with the `audience` column — every uploaded file is
public course material now, so there is no teacher-only chunk for a student to be
kept away from. The tenant filter is what remains, and it carries the whole
boundary.
"""

import uuid

import pytest

from backend.app.agent.legacy.graph import build_agent
from backend.app.models import Class, EMBEDDING_DIM, Lab, UserRole
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


def _rag_router():
    """A router_llm_fn that classifies every message as a concept (rag) query."""
    import json

    async def llm_fn(messages):
        return json.dumps({"route": "rag", "search_terms": []})
    return llm_fn


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
    body = "Parity and error detection in binary coding.\n"
    doc = await create_document(
        pg_session, class_id=cls.id, lab_id=lab.id, filename="cm.txt",
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
    )

    # Worker ingests: writes DocChunks (with tsv via to_tsvector).
    await ingest_document(pg_session, doc.id, embed_fn=_fixed_embed)

    # Agent answers a concept question end-to-end.
    agent = build_agent(pg_session, embed_fn=_fixed_embed, router_llm_fn=_rag_router())
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
    """The worker writes the tsvector with the document's language config
    (default fr), so a singular query matches the plural document via stemming.
    Ingest and query sides sharing the config is what keeps BM25 alive."""
    from backend.app.services.retrieval_service import bm25_search

    teacher, cls, lab = await _seed_lab(pg_session)
    body = "Les fonctions recursives sont puissantes et elegantes en pratique.\n"
    doc = await create_document(
        pg_session, class_id=cls.id, lab_id=lab.id, filename="cm.txt",
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
    )
    await ingest_document(pg_session, doc.id, embed_fn=_fixed_embed)

    hits = await bm25_search(pg_session, "fonction recursive", lab.id, language="fr")
    assert len(hits) == 1
    assert "fonctions recursives" in hits[0].content


@pytest.mark.asyncio
async def test_redline_cross_tenant_isolation(pg_session, tmp_path):
    teacher, cls, lab_a = await _seed_lab(pg_session, "Lab A")
    student = make_user(role=UserRole.student)
    pg_session.add(student)

    doc_a = await create_document(
        pg_session, class_id=cls.id, lab_id=lab_a.id, filename="a.txt",
        content=b"threads in lab A\n", uploaded_by=teacher.id,
        storage_root=tmp_path,
    )
    await ingest_document(pg_session, doc_a.id, embed_fn=_fixed_embed)

    # A second lab with its own chunk (cross-tenant bait).
    lab_b = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab B")
    pg_session.add(lab_b)
    await pg_session.flush()
    doc_b = await create_document(
        pg_session, class_id=cls.id, lab_id=lab_b.id, filename="b.txt",
        content=b"threads in lab B\n", uploaded_by=teacher.id,
        storage_root=tmp_path,
    )
    await ingest_document(pg_session, doc_b.id, embed_fn=_fixed_embed)
    await pg_session.commit()

    agent = build_agent(pg_session, embed_fn=_fixed_embed, router_llm_fn=_rag_router())
    result = await agent.ainvoke({
        "message": "Tell me about threads",
        "class_id": cls.id, "lab_id": lab_a.id, "user_id": student.id, "history": [],
    })

    context = " ".join(result.get("context_blocks", []))
    assert "lab A" in context
    assert "lab B" not in context
