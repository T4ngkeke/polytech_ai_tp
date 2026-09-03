"""
test_agent_graph.py — LangGraph agent end-to-end (no live model), CM-only.

One injected `router_llm_fn` (classify) picks between two routes — `rag` and
`direct` — and every decision is logged to RouterQueryLog. The compiled graph
routes, retrieves (lab-scoped), and assembles the final messages payload.

The `exercise` and `clarify` routes are gone with the Exercises table: there is
no number to resolve, so nothing to clarify and no sticky exercise to carry
between turns. The Socratic guardrail and the effort coaching that used to hang
off the exercise leg now apply to every route.

Uses pg_session where real pgvector retrieval is exercised, db_session (SQLite)
for routing and logging.
"""

import json
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent.graph import build_agent
from backend.app.models import (
    Class,
    CoachingLevel,
    DocChunk,
    Document,
    EMBEDDING_DIM,
    Lab,
    LearnerProfile,
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
                         lab_id=lab.id, chunk_index=0,
                         content=body, embedding=_unit(at), page_no=1))
    await session.commit()
    return cls, lab, student


# --- tracing ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_trace_captures_route(db_session):
    """An injected TraceBuilder records the final route for the AgentTraceLog."""
    tb = TraceBuilder()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="direct"),
                        trace=tb)
    await agent.ainvoke({
        "message": "hello there", "class_id": None, "lab_id": None,
        "user_id": uuid.uuid4(), "history": [],
    })

    assert tb.fields.get("route") == "direct"


@pytest.mark.asyncio
async def test_trace_flags_router_degradation(db_session):
    """A degraded router decision (garbage output → safe rag) is flagged."""
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


# --- retrieval --------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_rag_path_injects_retrieved_context(pg_session):
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "Threads share memory.", at=1)

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]  # aligns with the seeded chunk

    agent = build_agent(pg_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag", search_terms=["thread"]))
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
        router_llm_fn=_fake_router(route="rag"),
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
async def test_rag_embedding_failure_degrades_to_bm25_only(pg_session):
    """A dead embedding endpoint must not 500 the chat — rag degrades to
    BM25-only and flags it (only the main LLM + main DB are hard deps)."""
    cls, lab, student = await _seed_lab_with_chunk(pg_session, "note", at=1)
    tb = TraceBuilder()

    async def failing_embed(texts):
        raise RuntimeError("embedding endpoint down")

    agent = build_agent(pg_session, embed_fn=failing_embed,
                        router_llm_fn=_fake_router(route="rag"),
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
        router_llm_fn=_fake_router(route="rag"),
        grade_fn=bad_grade, rewrite_fn=rewrite, max_retries=1,
    )
    result = await agent.ainvoke({
        "message": "What is a thread?",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["route"] == "rag"
    assert result["low_evidence"] is True
    assert "[LOW EVIDENCE]" in result["messages_payload"][0]["content"]


# --- routing telemetry ------------------------------------------------------

@pytest.mark.asyncio
async def test_every_decision_is_logged(db_session):
    """Every router decision — not just low-confidence ones — is logged, so a
    future distilled router has a labelled source."""
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag"),
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
    assert rows[0].model_name == "ministral-9b"


@pytest.mark.asyncio
async def test_direct_decision_is_logged(db_session):
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="direct"))
    await agent.ainvoke({
        "message": "merci !",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    row = (await db_session.execute(select(RouterQueryLog))).scalars().one()
    assert row.route == "direct"


# --- coaching, now route-independent ----------------------------------------

@pytest.mark.asyncio
async def test_answer_seeking_guardrail_applies_on_rag(db_session):
    """The Socratic guardrail used to fire only on the exercise leg. With that leg
    gone it must follow the signal itself — a student offloading on a concept
    question gets the same treatment."""
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag", answer_seeking=True))
    result = await agent.ainvoke({
        "message": "just give me the answer",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    assert result["route"] == "rag"
    assert "ANSWER GUARDRAIL" in result["messages_payload"][0]["content"]


@pytest.mark.asyncio
async def test_effort_coaching_applies_off_the_old_exercise_leg(db_session):
    """Effort feeds the per-(student, lab) window on any route now. Uses the
    `direct` leg so this runs on SQLite — the rag leg needs real pgvector, and
    test_agent_tutor covers it there.

    The profile is pinned rather than earned: one low sample does not move the
    smoothed window far enough to change the level, and what is under test here
    is the wiring (coaching reaches the prompt off the old exercise leg), not
    the EMA."""
    cls, lab, student = await _seed_lab_with_chunk(db_session, "note", at=1)
    db_session.add(LearnerProfile(
        id=uuid.uuid4(), user_id=student.id, lab_id=lab.id,
        effort_ema=0.1, samples=5, coaching_level=CoachingLevel.low,
        teacher_override=True,
    ))
    await db_session.commit()

    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="direct", effort="low"))
    result = await agent.ainvoke({
        "message": "dunno", "class_id": cls.id, "lab_id": lab.id,
        "user_id": student.id, "history": [],
    })

    assert result["route"] == "direct"
    assert "[COACHING]" in result["messages_payload"][0]["content"]


@pytest.mark.asyncio
async def test_no_guardrail_without_the_signal(db_session):
    """The guardrail follows answer_seeking, not the route — absent the signal a
    plain question is answered plainly."""
    async def fake_embed(texts):
        return [_unit(1) for _ in texts]

    agent = build_agent(db_session, embed_fn=fake_embed,
                        router_llm_fn=_fake_router(route="rag", answer_seeking=False))
    result = await agent.ainvoke({
        "message": "what is recursion?",
        "class_id": None, "lab_id": None, "user_id": uuid.uuid4(), "history": [],
    })

    system = result["messages_payload"][0]["content"]
    assert "ANSWER GUARDRAIL" not in system
    assert "[COACHING]" not in system
