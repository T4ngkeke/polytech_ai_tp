"""
test_agent_graph.py — [v8.0] LangGraph agent end-to-end (no live model).

The router is one injected `router_llm_fn` (classify) → three-tier resolve_number
→ every decision logged to RouterQueryLog v2. Routes: exercise / rag / direct.
The compiled graph routes, retrieves (lab-scoped), and assembles the final
messages payload. Uses pg_session where real pgvector retrieval is exercised, and
db_session (SQLite) for exercise-lookup / routing / logging.
"""

import json
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent.graph import build_agent
from backend.app.models import (
    Audience,
    Class,
    DocChunk,
    Document,
    EMBEDDING_DIM,
    Exercise,
    Lab,
    RouterQueryLog,
    UserRole,
)
from backend.app.services.trace_service import TraceBuilder
from backend.tests.conftest import make_user


def _unit(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


def _fake_router(**payload):
    """A router_llm_fn that returns a fixed classify JSON payload."""
    payload.setdefault("search_terms", [])
    payload.setdefault("sticky_matches", False)
    payload.setdefault("number_source", "none" if payload.get("exercise_number") is None else "explicit")

    async def llm_fn(messages):
        return json.dumps(payload)
    return llm_fn


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


async def _seed_bare_lab(session):
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
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="d.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex, uploaded_by=teacher.id)
    session.add(doc)
    await session.flush()
    return cls, lab, doc, teacher


@pytest.mark.asyncio
async def test_trace_captures_route(db_session):
    """[v8.0] An injected TraceBuilder records the final route for the AgentTraceLog."""
    tb = TraceBuilder()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="direct", exercise_number=None),
                        trace=tb)
    await agent.ainvoke({
        "message": "hello there", "class_id": None, "lab_id": None,
        "user_id": uuid.uuid4(), "history": [],
    })

    assert tb.fields.get("route") == "direct"


@pytest.mark.asyncio
async def test_trace_flags_router_degradation(db_session):
    """A degraded router decision (garbage output → safe rag) is flagged in the trace."""
    tb = TraceBuilder()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    async def garbage_router(messages):
        return "not json at all"

    agent = build_agent(db_session, embed_fn=fake_embed, router_llm_fn=garbage_router,
                        trace=tb)
    await agent.ainvoke({
        "message": "???", "class_id": None, "lab_id": None,
        "user_id": uuid.uuid4(), "history": [],
    })

    assert tb.fields.get("route") == "rag"
    assert tb.degraded_flags.get("router") is True


@pytest.mark.asyncio
async def test_agent_rag_path_injects_retrieved_context(pg_session):
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "Threads share memory.", at=1)

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]  # aligns with the seeded chunk

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag", exercise_number=None,
                                                   search_terms=["thread"]))
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
    # Context framing + citation discipline: "possibly relevant, ignore if not".
    assert "ignore" in system["content"].lower()
    assert result["messages_payload"][-1] == {"role": "user", "content": "What is a thread?"}


@pytest.mark.asyncio
async def test_agent_exercise_path_surfaces_statement_only(pg_session):
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "ignored", at=0)
    doc = (await pg_session.execute(
        Document.__table__.select().where(Document.lab_id == lab.id)
    )).first()
    pg_session.add(Exercise(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        number="Exercise 1", number_normalized=1, statement="Sum two numbers.", hints="use +",
    ))
    await pg_session.commit()

    async def fake_embed(texts):
        return [_unit(0) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=1,
                                                   number_source="explicit"))
    result = await agent.ainvoke({
        "message": "How do I do exercise 1?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "exercise"
    system = result["messages_payload"][0]["content"]
    # Only the student-safe statement is surfaced; no solution exists to leak.
    assert "Sum two numbers." in system


@pytest.mark.asyncio
async def test_exercise_path_supplements_with_cm_context(pg_session):
    """[v8.0] After an exercise hit, the graph pulls related course-material (CM)
    concepts via hybrid_search and injects them alongside the statement — and CM
    material that is used MUST be cited (steering students back to the slides)."""
    cls, lab, student = await _seed_lab_with_chunk(
        pg_session, "Merge sort divides the list into halves.", at=1)
    doc = (await pg_session.execute(
        Document.__table__.select().where(Document.lab_id == lab.id)
    )).first()
    pg_session.add(Exercise(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        number="Exercise 1", number_normalized=1, statement="Implement merge sort.",
    ))
    await pg_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]  # aligns with the seeded CM chunk

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=1,
                                                   number_source="explicit"))
    result = await agent.ainvoke({
        "message": "how do I do exercise 1?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "exercise"
    system = result["messages_payload"][0]["content"]
    assert "Implement merge sort." in system         # the exercise statement
    assert "Merge sort divides the list" in system    # supplemented CM concept
    assert result["citations"]                        # CM used → citation mandatory


@pytest.mark.asyncio
async def test_exercise_followup_supplement_query_includes_search_terms(pg_session):
    """On a sticky follow-up, the CM-supplement query = statement + the follow-up's
    search_terms, so retrieval focus moves with the question."""
    cls, lab, doc, teacher = await _seed_bare_lab(pg_session)
    pg_session.add(Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                            number="Exercise 1", number_normalized=1, statement="Base statement"))
    await pg_session.commit()

    captured: list[str] = []

    async def fake_embed(texts):
        captured.extend(texts)
        return [_unit(1) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=None,
                                                   number_source="none", sticky_matches=True,
                                                   search_terms=["multithreading", "deadlock"]))
    await agent.ainvoke({
        "message": "why does this use multithreading?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
        "sticky_number": 1, "sticky_excerpt": "Base statement",
    })

    supplement_query = captured[-1]
    assert "Base statement" in supplement_query
    assert "multithreading" in supplement_query


@pytest.mark.asyncio
async def test_every_decision_is_logged(db_session):
    """[v8.0] Every router decision — not just low-confidence ones — is logged to
    RouterQueryLog v2 with route + resolved number source."""
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag", exercise_number=None),
                        router_model_name="ministral-9b")
    result = await agent.ainvoke({
        "message": "just chatting about nothing",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    assert result["route"] == "rag"
    rows = (await db_session.execute(select(RouterQueryLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "just chatting about nothing"
    assert rows[0].route == "rag"
    assert rows[0].number_source == "none"
    assert rows[0].model_name == "ministral-9b"


@pytest.mark.asyncio
async def test_exercise_decision_logs_number_and_source(db_session):
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=3,
                                                   number_source="explicit"))
    await agent.ainvoke({
        "message": "exercice 3 ?",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    row = (await db_session.execute(select(RouterQueryLog))).scalars().one()
    assert row.route == "exercise"
    assert row.exercise_number == 3
    assert row.number_source == "explicit"


@pytest.mark.asyncio
async def test_exercise_number_filters_exercises(pg_session):
    """The resolved exercise number filters the search — only that exercise
    surfaces, not the whole lab. (pg: the exercise path now supplements with CM.)"""
    cls, lab, doc, teacher = await _seed_bare_lab(pg_session)
    pg_session.add_all([
        Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 number="Exercise II", number_normalized=2, statement="SECOND exercise"),
        Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 number="Exercise III", number_normalized=3, statement="THIRD exercise"),
    ])
    await pg_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=2,
                                                   number_source="explicit"))
    result = await agent.ainvoke({
        "message": "comment faire l'exercice II ?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
    })

    assert result["route"] == "exercise"
    assert result["exercise_number"] == 2
    blocks = "\n".join(result["context_blocks"])
    assert "SECOND exercise" in blocks
    assert "THIRD exercise" not in blocks


@pytest.mark.asyncio
async def test_student_cannot_reference_teacher_audience_exercise(db_session):
    """[red line] A teacher-audience exercise is absent from the student's number
    list, so asking for it routes to clarify — teacher content never surfaces, and
    only the student's own exercises are offered."""
    cls, lab, doc, teacher = await _seed_bare_lab(db_session)
    db_session.add_all([
        Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 audience=Audience.student, number="E1", number_normalized=1,
                 statement="STUDENT-SAFE exercise"),
        Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                 audience=Audience.teacher, number="E2", number_normalized=2,
                 statement="TEACHER-ONLY exercise"),
    ])
    await db_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=2,
                                                   number_source="explicit"))
    result = await agent.ainvoke({
        "message": "exercice 2 svp",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
    })

    assert result["route"] == "clarify"
    system = result["messages_payload"][0]["content"]
    assert "TEACHER-ONLY exercise" not in system
    assert "E1" in system  # only the student's own exercise is offered


@pytest.mark.asyncio
async def test_unknown_exercise_number_routes_to_clarify(db_session):
    """Explicit number not in this lab → clarify (with the available number list),
    never a wrong-exercise answer."""
    cls, lab, doc, teacher = await _seed_bare_lab(db_session)
    db_session.add(Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                            number="Exercise 1", number_normalized=1, statement="only one"))
    await db_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=9,
                                                   number_source="explicit"))
    result = await agent.ainvoke({
        "message": "how do I do exercise 9?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
    })

    assert result["route"] == "clarify"
    system = result["messages_payload"][0]["content"]
    assert "Exercise 1" in system  # the available number is offered


@pytest.mark.asyncio
async def test_exercise_route_without_number_clarifies(db_session):
    """route=exercise but no number and no sticky → clarify, never a guess."""
    cls, lab, doc, teacher = await _seed_bare_lab(db_session)
    db_session.add(Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                            number="Exercise 1", number_normalized=1, statement="only one"))
    await db_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=None,
                                                   number_source="none"))
    result = await agent.ainvoke({
        "message": "how do I do this exercise?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
    })

    assert result["route"] == "clarify"


@pytest.mark.asyncio
async def test_clarify_anti_loop_falls_through_to_rag(pg_session):
    """A second consecutive unresolved turn does not clarify again — it falls
    through to rag rather than looping."""
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "note", at=1)

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=9,
                                                   number_source="explicit"))
    result = await agent.ainvoke({
        "message": "still exercise 9",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
        "prev_was_clarify": True,
    })

    assert result["route"] == "rag"


@pytest.mark.asyncio
async def test_sticky_fills_missing_number_and_routes_exercise(pg_session):
    """No number in the message, but the sticky exercise matches → resolve_number
    fills it (source=sticky), it validates, and the exercise is surfaced — a
    follow-up like 'and the next part?' reconnects to the current exercise."""
    cls, lab, doc, teacher = await _seed_bare_lab(pg_session)
    pg_session.add(Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                            number="Exercise 1", number_normalized=1, statement="the sticky one"))
    await pg_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=None,
                                                   number_source="none", sticky_matches=True))
    result = await agent.ainvoke({
        "message": "et la question suivante ?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
        "sticky_number": 1, "sticky_excerpt": "the sticky one",
    })

    assert result["route"] == "exercise"
    assert result["exercise_number"] == 1
    assert "the sticky one" in result["messages_payload"][0]["content"]


@pytest.mark.asyncio
async def test_all_filtered_triggers_zero_context_disclaimer(pg_session):
    """When the rerank threshold drops everything, rag injects NO context and NO
    citations, and states plainly that the course material doesn't cover this —
    never a confident answer with fake citations."""
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "unrelated slide text", at=1)

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    async def low_rerank(query, docs):
        return [0.0 for _ in docs]  # everything scores below the threshold

    agent = build_agent(
        pg_session, embed_fn=fake_embed,
        router_llm_fn=_fake_router(route="rag", exercise_number=None),
        rerank_fn=low_rerank, rerank_score_threshold=0.5,
    )
    result = await agent.ainvoke({
        "message": "What is quantum entanglement?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "rag"
    system = result["messages_payload"][0]["content"]
    assert "NO COURSE MATERIAL" in system          # zero-context disclaimer branch
    assert "unrelated slide text" not in system     # nothing injected
    assert not result.get("citations")              # nothing used → no citations


@pytest.mark.asyncio
async def test_exercise_leg_applies_answer_seeking_guardrail_from_router(pg_session):
    """[v8.0] The Socratic guardrail is now driven by the router's answer_seeking
    (not a regex). A benign-looking message the old regex would miss still triggers
    the guardrail on the exercise leg when the router flags it."""
    cls, lab, doc, teacher = await _seed_bare_lab(pg_session)
    pg_session.add(Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
                            number="Exercise 1", number_normalized=1, statement="s1"))
    await pg_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="exercise", exercise_number=1,
                                                   number_source="explicit", answer_seeking=True))
    result = await agent.ainvoke({
        "message": "let's look at exercise 1",   # no demand phrase → old regex would NOT flag
        "class_id": cls.id, "lab_id": lab.id, "user_id": teacher.id, "history": [],
    })

    assert result["route"] == "exercise"
    assert "ANSWER GUARDRAIL" in result["messages_payload"][0]["content"]


@pytest.mark.asyncio
async def test_non_exercise_route_ignores_coaching_signals(db_session):
    """[v8.0] Coaching + guardrail are exercise-only. A message the regex WOULD flag
    as answer-seeking, routed to rag, must NOT get the Socratic guardrail — the
    signal is not propagated off the exercise leg."""
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]  # unused: no-lab rag early-returns

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag", exercise_number=None,
                                                   answer_seeking=True, effort="low"))
    result = await agent.ainvoke({
        "message": "just give me the answer",   # regex WOULD flag this
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    assert result["route"] == "rag"
    system = result["messages_payload"][0]["content"]
    assert "ANSWER GUARDRAIL" not in system
    assert "[COACHING]" not in system


@pytest.mark.asyncio
async def test_rag_embedding_failure_degrades_to_bm25_only(pg_session):
    """[v8.0] A dead embedding endpoint must not 500 the chat — rag degrades to
    BM25-only and flags it in the trace (only the main LLM + main DB are hard deps)."""
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "note", at=1)
    tb = TraceBuilder()

    async def failing_embed(texts):
        raise RuntimeError("embedding endpoint down")

    agent = build_agent(pg_session, embed_fn=failing_embed,
                        router_llm_fn=_fake_router(route="rag", exercise_number=None),
                        trace=tb)
    result = await agent.ainvoke({
        "message": "What is a thread?", "class_id": cls.id, "lab_id": lab.id,
        "user_id": student.id, "history": [],
    })

    assert result["route"] == "rag"                       # did not blow up
    assert tb.degraded_flags.get("embedding") is True     # degradation recorded


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
        router_llm_fn=_fake_router(route="rag", exercise_number=None),
        grade_fn=bad_grade, rewrite_fn=rewrite, max_retries=1,
    )
    result = await agent.ainvoke({
        "message": "What is a thread?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "rag"
    assert result["low_evidence"] is True
    assert "[LOW EVIDENCE]" in result["messages_payload"][0]["content"]
