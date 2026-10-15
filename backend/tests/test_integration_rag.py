"""
test_integration_rag.py — end-to-end ship gate.

Runs ingestion and search on live pgvector with fake model functions:
  ingest_document (parse → chunk → contextual → embed → tsv)
    → hybrid_search (vector + BM25 over the tsv the worker wrote).

Plus the red line that must hold before ship: cross-tenant isolation, enforced
in the SQL WHERE clause rather than in a prompt.

The audience red line is gone with the `audience` column — every uploaded file is
public course material now, so there is no teacher-only chunk for a student to be
kept away from. The tenant filter is what remains, and it carries the whole
boundary.
"""

import uuid

import pytest

from backend.app.models import Class, EMBEDDING_DIM, Lab, UserRole
from backend.app.services.document_service import create_document
from backend.app.services.retrieval_service import hybrid_search
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

    hits, _ = await hybrid_search(pg_session, "Tell me about threads", _unit(1), lab_a.id,
                                  class_id=cls.id, language="en")
    context = " ".join(h.content for h in hits)
    assert "lab A" in context
    assert "lab B" not in context
