"""
test_retrieval_hybrid_pg.py — [v7.1] hybrid retrieval against live pgvector.

Exercises the real SQL: vector ANN + BM25 (tsvector) recall, RRF fusion, and the
tenant + audience filter enforced in the WHERE clause. Skips unless a pgvector
Postgres is reachable (TEST_PG_URL). Model calls are faked; the reranker is faked.
"""

import uuid

import pytest
from sqlalchemy import func

from backend.app.models import (
    Audience,
    Class,
    DocChunk,
    Document,
    DocType,
    EMBEDDING_DIM,
    Lab,
    UserRole,
)
from backend.app.services.retrieval_service import bm25_search, hybrid_search
from backend.tests.conftest import make_user


def _unit(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


async def _seed_lab(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    doc = Document(
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="d.pdf",
        storage_path="/data/d.pdf", content_hash=uuid.uuid4().hex,
        doc_type=DocType.CM, audience=Audience.student, uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()
    return cls, lab, doc


def _chunk(doc, cls, lab, *, idx, content, emb, audience=Audience.student,
           ts_config="french"):
    return DocChunk(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        doc_type=DocType.CM, audience=audience, chunk_index=idx,
        content=content, embedding=emb, page_no=idx + 1,
        tsv=func.to_tsvector(ts_config, content),
    )


@pytest.mark.asyncio
async def test_hybrid_search_returns_lab_scoped_relevant_chunk(pg_session):
    cls, lab, doc = await _seed_lab(pg_session)
    pg_session.add_all([
        _chunk(doc, cls, lab, idx=0, content="about pointers and memory", emb=_unit(0)),
        _chunk(doc, cls, lab, idx=1, content="about threads and concurrency", emb=_unit(1)),
        _chunk(doc, cls, lab, idx=2, content="about sorting algorithms", emb=_unit(2)),
    ])
    await pg_session.commit()

    hits, _all_filtered = await hybrid_search(
        pg_session, query_text="threads", query_embedding=_unit(1),
        lab_id=lab.id, audience=Audience.student, top_k=2,
    )

    assert hits[0].content == "about threads and concurrency"
    assert all(h.document_id == doc.id for h in hits)


@pytest.mark.asyncio
async def test_hybrid_search_excludes_teacher_audience_for_student(pg_session):
    cls, lab, doc = await _seed_lab(pg_session)
    pg_session.add_all([
        _chunk(doc, cls, lab, idx=0, content="student-visible threads notes", emb=_unit(1)),
        _chunk(doc, cls, lab, idx=1, content="teacher-only threads answer key",
               emb=_unit(1), audience=Audience.teacher),
    ])
    await pg_session.commit()

    hits, _all_filtered = await hybrid_search(
        pg_session, query_text="threads", query_embedding=_unit(1),
        lab_id=lab.id, audience=Audience.student, top_k=5,
    )

    contents = [h.content for h in hits]
    assert "student-visible threads notes" in contents
    assert "teacher-only threads answer key" not in contents


@pytest.mark.asyncio
async def test_hybrid_search_does_not_leak_across_labs(pg_session):
    cls, lab_a, doc_a = await _seed_lab(pg_session)
    lab_b = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 2")
    pg_session.add(lab_b)
    await pg_session.flush()
    doc_b = Document(
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab_b.id, filename="b.pdf",
        storage_path="/data/b.pdf", content_hash=uuid.uuid4().hex,
        doc_type=DocType.CM, audience=Audience.student, uploaded_by=doc_a.uploaded_by,
    )
    pg_session.add(doc_b)
    await pg_session.flush()
    pg_session.add_all([
        _chunk(doc_a, cls, lab_a, idx=0, content="lab A threads", emb=_unit(1)),
        _chunk(doc_b, cls, lab_b, idx=0, content="lab B threads", emb=_unit(1)),
    ])
    await pg_session.commit()

    hits, _all_filtered = await hybrid_search(
        pg_session, query_text="threads", query_embedding=_unit(1),
        lab_id=lab_a.id, audience=Audience.student, top_k=5,
    )

    assert [h.content for h in hits] == ["lab A threads"]


# --- [v7.3] BM25 OR semantics + language config --------------------------------
# plainto_tsquery is AND: one query word missing from the chunk → zero recall.
# Natural-language French questions always carry extra words, so the old
# ('simple' + AND) BM25 leg was silently dead. OR semantics + French stemming.

@pytest.mark.asyncio
async def test_bm25_or_matches_despite_extra_query_words(pg_session):
    cls, lab, doc = await _seed_lab(pg_session)
    pg_session.add(_chunk(
        doc, cls, lab, idx=0,
        content="La recursivite est une methode ou une fonction s'appelle elle-meme.",
        emb=_unit(0),
    ))
    await pg_session.commit()

    # 'informatique' does not appear in the chunk — AND semantics returns nothing.
    hits = await bm25_search(
        pg_session, "Qu'est-ce que la recursivite en informatique ?",
        lab.id, audience=Audience.student, language="fr",
    )

    assert len(hits) == 1
    assert "recursivite" in hits[0].content


@pytest.mark.asyncio
async def test_bm25_french_stemming_matches_inflections(pg_session):
    cls, lab, doc = await _seed_lab(pg_session)
    pg_session.add(_chunk(
        doc, cls, lab, idx=0,
        content="Les fonctions recursives sont puissantes et elegantes.",
        emb=_unit(0),
    ))
    await pg_session.commit()

    # Singular query vs plural document — French stemming bridges the gap.
    hits = await bm25_search(
        pg_session, "fonction recursive",
        lab.id, audience=Audience.student, language="fr",
    )

    assert len(hits) == 1


@pytest.mark.asyncio
async def test_bm25_stopword_only_query_returns_empty(pg_session):
    cls, lab, doc = await _seed_lab(pg_session)
    pg_session.add(_chunk(doc, cls, lab, idx=0, content="contenu reel", emb=_unit(0)))
    await pg_session.commit()

    hits = await bm25_search(
        pg_session, "de la les", lab.id, audience=Audience.student, language="fr",
    )
    assert hits == []


@pytest.mark.asyncio
async def test_hybrid_search_applies_reranker(pg_session):
    cls, lab, doc = await _seed_lab(pg_session)
    pg_session.add_all([
        _chunk(doc, cls, lab, idx=0, content="threads alpha", emb=_unit(1)),
        _chunk(doc, cls, lab, idx=1, content="threads beta", emb=_unit(1)),
    ])
    await pg_session.commit()

    async def fake_rerank(query, documents):
        # Prefer the "beta" chunk regardless of recall order.
        return [1.0 if "beta" in d else 0.0 for d in documents]

    hits, _all_filtered = await hybrid_search(
        pg_session, query_text="threads", query_embedding=_unit(1),
        lab_id=lab.id, audience=Audience.student, rerank_fn=fake_rerank, top_k=1,
    )

    assert len(hits) == 1
    assert hits[0].content == "threads beta"
