"""
test_retrieval.py — retrieval service tests, CM-only.

Runs vector RAG and hybrid search against a live pgvector Postgres (via the
`pg_session` fixture). Structured exercise search is gone with the Exercises
table; what remains is chunk retrieval over course material.

The tenant red line is unchanged and still the point of these tests: a query
never returns another lab's or another class's data, and the filter lives in the
SQL WHERE clause rather than in a prompt.
"""

import uuid

import pytest

from backend.app.models import (
    Class,
    Document,
    EMBEDDING_DIM,
    Lab,
    UserRole,
)
from backend.app.services.retrieval_service import hybrid_search, vector_search
from backend.tests.conftest import add_chunk, add_current_revision, make_user


def _unit(index: int) -> list[float]:
    """A unit embedding with a single non-zero component (orthogonal basis)."""
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


async def _seed_class_with_lab(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(
        id=uuid.uuid4(), name="Algorithms",
        teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6],
    )
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Threads Lab")
    session.add(lab)
    await session.flush()
    doc = Document(
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
        storage_path="/data/documents/cm.pdf", content_hash=uuid.uuid4().hex,
        uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()
    await add_current_revision(session, doc, teacher.id)
    return cls, lab, doc


@pytest.mark.asyncio
async def test_vector_search_returns_nearest_chunk_by_cosine(pg_session):
    cls, lab, doc = await _seed_class_with_lab(pg_session)
    for i, body in enumerate(["about pointers", "about threads", "about sorting"]):
        await add_chunk(pg_session, doc, content=body, page_no=i + 1, chunk_index=i, embedding=_unit(i))
    await pg_session.commit()

    # Query embedding aligned with the second chunk ("about threads").
    hits = await vector_search(pg_session, query_embedding=_unit(1), lab_id=lab.id, k=2)

    assert len(hits) == 2
    assert hits[0].content == "about threads"   # nearest first
    assert hits[0].page_no == 2


@pytest.mark.asyncio
async def test_vector_search_does_not_leak_across_labs(pg_session):
    """Tenant isolation, in SQL. Previously covered on the exercise path; with
    exercises gone, chunks carry the whole boundary."""
    cls, lab_a, doc_a = await _seed_class_with_lab(pg_session)
    lab_b = Lab(id=uuid.uuid4(), class_id=cls.id, name="Sorting Lab")
    pg_session.add(lab_b)
    await pg_session.flush()
    doc_b = Document(
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab_b.id, filename="b.pdf",
        storage_path="/data/documents/b.pdf", content_hash=uuid.uuid4().hex,
        uploaded_by=doc_a.uploaded_by,
    )
    pg_session.add(doc_b)
    await pg_session.flush()
    await add_current_revision(pg_session, doc_b, doc_a.uploaded_by)
    await add_chunk(pg_session, doc_a, content="lab A note", embedding=_unit(1))
    await add_chunk(pg_session, doc_b, content="lab B note", embedding=_unit(1))
    await pg_session.commit()

    hits = await vector_search(pg_session, query_embedding=_unit(1), lab_id=lab_a.id, k=10)

    assert [h.content for h in hits] == ["lab A note"]


@pytest.mark.asyncio
async def test_hybrid_search_includes_class_wide_shared_cm(pg_session):
    """A class-wide shared chunk (lab_id=NULL) is retrievable within any lab of
    the same class, but never leaks across classes."""
    cls, lab_a, doc_a = await _seed_class_with_lab(pg_session)
    # class-wide shared (lab_id NULL) in this class, then lab-specific
    await add_chunk(pg_session, doc_a, content="shared course note", lab_id=None, embedding=_unit(1))
    await add_chunk(pg_session, doc_a, content="lab A note", chunk_index=1, embedding=_unit(1))
    cls2, _lab2, doc2 = await _seed_class_with_lab(pg_session)
    # another class's shared chunk — cross-tenant bait
    await add_chunk(pg_session, doc2, content="other class note", lab_id=None, embedding=_unit(1))
    await pg_session.commit()

    hits, _ = await hybrid_search(pg_session, "note", _unit(1), lab_id=lab_a.id,
                                  class_id=cls.id)
    contents = {h.content for h in hits}
    assert "shared course note" in contents    # class-wide shared reachable
    assert "lab A note" in contents             # lab-specific reachable
    assert "other class note" not in contents   # class boundary holds
