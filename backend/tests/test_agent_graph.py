"""
test_agent_graph.py — v7 LangGraph agent end-to-end (no LLM).

The compiled graph routes, retrieves (lab-scoped), and assembles the final
messages payload. Streaming the payload to the LLM happens in the endpoint.
Uses pg_session (rag_search needs real pgvector) + a fake query embedder.
"""

import uuid

import pytest

from backend.app.agent.graph import build_agent
from backend.app.models import (
    Audience,
    Class,
    DocChunk,
    Document,
    EMBEDDING_DIM,
    Exercise,
    Lab,
    UserRole,
)
from backend.tests.conftest import make_user


def _unit(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


async def _seed_lab_with_chunk(session, body: str, at: int):
    teacher = make_user(role=UserRole.teacher)
    student = make_user(role=UserRole.student)
    session.add_all([teacher, student])
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="d.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex, uploaded_by=teacher.id)
    session.add(doc)
    await session.flush()
    session.add(DocChunk(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id,
                         lab_id=lab.id, audience=Audience.student, chunk_index=0,
                         content=body, embedding=_unit(at), page_no=1))
    await session.commit()
    return cls, lab, student


@pytest.mark.asyncio
async def test_agent_rag_path_injects_retrieved_context(pg_session):
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "Threads share memory.", at=1)

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]  # aligns with the seeded chunk

    agent = build_agent(pg_session, embed_fn=fake_embed)
    result = await agent.ainvoke({
        "message": "What is a thread?",
        "class_id": cls.id,
        "lab_id": lab.id,
        "user_id": student.id,
        "history": [],
    })

    assert result["route"] == "rag"
    system = result["messages_payload"][0]
    assert system["role"] == "system"
    assert "Threads share memory." in system["content"]
    # last payload entry is the user's question
    assert result["messages_payload"][-1] == {"role": "user", "content": "What is a thread?"}


@pytest.mark.asyncio
async def test_agent_agentic_search_surfaces_statement_only(pg_session):
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "ignored", at=0)
    # attach an exercise to the same lab
    doc = (await pg_session.execute(
        Document.__table__.select().where(Document.lab_id == lab.id)
    )).first()
    pg_session.add(Exercise(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        number="Exercise 1", statement="Sum two numbers.", hints="use +",
    ))
    await pg_session.commit()

    async def fake_embed(texts):
        return [_unit(0) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed)
    result = await agent.ainvoke({
        "message": "How do I do exercise 1?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "agentic_search"
    system = result["messages_payload"][0]["content"]
    # Only the student-safe statement is surfaced; no solution exists to leak.
    assert "Sum two numbers." in system


@pytest.mark.asyncio
async def test_low_confidence_query_is_logged(db_session):
    """A below-threshold route falls back to rag and is logged (Phase 2.3)."""
    from sqlalchemy import select

    from backend.app.agent.router import INTENT_EXEMPLARS
    from backend.app.models import RouterQueryLog

    anchors = set(INTENT_EXEMPLARS["rag"]) | set(INTENT_EXEMPLARS["direct"])

    async def fake_embed(texts):
        # Anchor exemplars on one axis; the actual query orthogonal (cosine 0).
        return [[1.0, 0.0] if t in anchors else [0.0, 1.0] for t in texts]

    agent = build_agent(db_session, embed_fn=fake_embed, router_threshold=0.5)
    result = await agent.ainvoke({
        "message": "??? message totally ambiguous",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    assert result["route"] == "rag"  # safe fallback
    rows = (await db_session.execute(select(RouterQueryLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "??? message totally ambiguous"
    assert rows[0].chosen_route == "rag"


@pytest.mark.asyncio
async def test_llm_fallback_routes_roman_exercise_to_agentic_search(db_session):
    """[v7.2] Regex misses 'exercise II' (no digit), but it is exercise-shaped, so
    the ROUTER_MODEL fallback extracts the number → route to agentic_search."""
    async def fake_embed(texts):
        return [[0.0, 1.0] for _ in texts]

    calls: list[str] = []

    async def fake_exercise_extract(message):
        calls.append(message)
        return 2  # the model resolves "exercise II" → 2

    agent = build_agent(
        db_session, embed_fn=fake_embed, router_threshold=0.5,
        exercise_extract_fn=fake_exercise_extract,
    )
    result = await agent.ainvoke({
        "message": "how do I start exercise II?",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    assert result["route"] == "agentic_search"
    assert calls == ["how do I start exercise II?"]


@pytest.mark.asyncio
async def test_llm_fallback_not_called_when_regex_hits(db_session):
    """The deterministic fast-path keeps zero latency — no LLM call when it hits."""
    async def fake_embed(texts):
        return [[0.0, 1.0] for _ in texts]

    calls: list[str] = []

    async def fake_exercise_extract(message):
        calls.append(message)
        return 2

    agent = build_agent(
        db_session, embed_fn=fake_embed, router_threshold=0.5,
        exercise_extract_fn=fake_exercise_extract,
    )
    result = await agent.ainvoke({
        "message": "how do I do exercise 2?",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    assert result["route"] == "agentic_search"
    assert calls == []  # regex hit → no LLM fallback


@pytest.mark.asyncio
async def test_llm_fallback_number_filters_exercises(db_session):
    """[v7.2 fix] the resolved exercise number must actually filter the search —
    'exercice II' surfaces only exercise II, not the whole lab."""
    from backend.app.models import Document

    teacher = make_user(role=UserRole.teacher)
    db_session.add(teacher)
    await db_session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    db_session.add(lab)
    await db_session.flush()
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="d.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex, uploaded_by=teacher.id)
    db_session.add(doc)
    await db_session.flush()
    db_session.add_all([
        Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 number="Exercise II", number_normalized=2, statement="SECOND exercise"),
        Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 number="Exercise III", number_normalized=3, statement="THIRD exercise"),
    ])
    await db_session.commit()

    async def fake_embed(texts):
        return [[0.0, 1.0] for _ in texts]

    async def fake_exercise_extract(_message):
        return 2

    agent = build_agent(
        db_session, embed_fn=fake_embed, router_threshold=0.5,
        exercise_extract_fn=fake_exercise_extract,
    )
    result = await agent.ainvoke({
        "message": "comment faire l'exercice II ?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
    })

    assert result["route"] == "agentic_search"
    assert result["exercise_number"] == 2
    blocks = "\n".join(result["context_blocks"])
    assert "SECOND exercise" in blocks
    assert "THIRD exercise" not in blocks


@pytest.mark.asyncio
async def test_self_eval_bad_verdict_adds_low_evidence_disclaimer(pg_session):
    """A persistently-bad self-eval verdict raises the low-evidence disclaimer."""
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "barely relevant note", at=1)

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    async def bad_grade(query, docs):
        return "bad"

    async def rewrite(query):
        return query + " rewritten"

    agent = build_agent(
        pg_session, embed_fn=fake_embed,
        grade_fn=bad_grade, rewrite_fn=rewrite, max_retries=1,
    )
    result = await agent.ainvoke({
        "message": "What is a thread?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "rag"
    assert result["low_evidence"] is True
    assert "[LOW EVIDENCE]" in result["messages_payload"][0]["content"]
