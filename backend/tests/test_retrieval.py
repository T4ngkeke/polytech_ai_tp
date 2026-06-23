"""
test_retrieval.py — v7 retrieval service tests.

Exercises structured exercise search and vector RAG against a live pgvector
Postgres (via the `pg_session` fixture). Enforces two red lines:
  * tenant isolation — never return another lab's data
  * `solution` is never surfaced by retrieval
"""

import uuid

import pytest

from backend.app.models import (
    Class,
    DocChunk,
    Document,
    Exercise,
    EMBEDDING_DIM,
    Lab,
    UserRole,
)
from backend.app.services.retrieval_service import rag_search, search_exercises
from backend.tests.conftest import make_user


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
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
        storage_path="/data/documents/td.pdf", content_hash=uuid.uuid4().hex,
        uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()
    return cls, lab, doc


@pytest.mark.asyncio
async def test_search_exercises_is_lab_scoped_and_solution_free(pg_session):
    cls, lab, doc = await _seed_class_with_lab(pg_session)
    ex = Exercise(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        number="Exercise 2", statement="Implement a thread-safe counter.",
        hints="Think about locks.",
    )
    pg_session.add(ex)
    await pg_session.commit()

    hits = await search_exercises(pg_session, lab_id=lab.id)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.number == "Exercise 2"
    assert hit.statement == "Implement a thread-safe counter."
    # Red line: there is no solution to surface (the column does not exist).
    assert not hasattr(hit, "solution")


@pytest.mark.asyncio
async def test_search_exercises_does_not_leak_across_labs(pg_session):
    cls, lab_a, doc_a = await _seed_class_with_lab(pg_session)
    # A second lab in the same class, with its own exercise.
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

    pg_session.add(Exercise(
        id=uuid.uuid4(), document_id=doc_a.id, class_id=cls.id, lab_id=lab_a.id,
        number="A1", statement="lab A exercise",
    ))
    pg_session.add(Exercise(
        id=uuid.uuid4(), document_id=doc_b.id, class_id=cls.id, lab_id=lab_b.id,
        number="B1", statement="lab B exercise",
    ))
    await pg_session.commit()

    hits = await search_exercises(pg_session, lab_id=lab_a.id)

    assert [h.number for h in hits] == ["A1"]


@pytest.mark.asyncio
async def test_rag_search_returns_nearest_chunk_by_cosine(pg_session):
    cls, lab, doc = await _seed_class_with_lab(pg_session)
    for i, body in enumerate(["about pointers", "about threads", "about sorting"]):
        pg_session.add(DocChunk(
            id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
            chunk_index=i, content=body, embedding=_unit(i), page_no=i + 1,
        ))
    await pg_session.commit()

    # Query embedding aligned with the second chunk ("about threads").
    hits = await rag_search(pg_session, query_embedding=_unit(1), lab_id=lab.id, k=2)

    assert len(hits) == 2
    assert hits[0].content == "about threads"   # nearest first
    assert hits[0].page_no == 2
