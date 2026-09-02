"""
test_agent_tutor.py — coaching wired into the agent graph.

Coaching (the router's effort → smoothed level → strategy) and the Socratic
answer-seeking guardrail are driven by the router's structured signals.

They used to fire only on the exercise leg. That leg is gone, and a student can
offload on a concept question just as easily as on an exercise, so the signals
now apply on whatever route the message took. These run real retrieval, so they
use pg_session.
"""

import json
import uuid

import pytest

from sqlalchemy import select

from backend.app.models import (
    Class,
    CoachingLevel,
    DocChunk,
    Document,
    EMBEDDING_DIM,
    Lab,
    LearnerProfile,
    UserRole,
)
from backend.app.agent.graph import build_agent
from backend.tests.conftest import make_user


def _unit(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


async def _fake_embed(texts):
    return [_unit(1) for _ in texts]


def _fake_router(route="rag", **extra):
    payload = {"route": route, "search_terms": []}
    payload.update(extra)

    async def llm_fn(messages):
        return json.dumps(payload)
    return llm_fn


async def _seed_lab(session):
    student = make_user(role=UserRole.student)
    teacher = make_user(role=UserRole.teacher)
    session.add_all([student, teacher])
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
                         content="Merge sort divides the list in half.",
                         embedding=_unit(1), page_no=1))
    await session.commit()
    return student, cls, lab


@pytest.mark.asyncio
async def test_answer_seeking_triggers_socratic_guardrail(pg_session):
    student, cls, lab = await _seed_lab(pg_session)

    agent = build_agent(pg_session, embed_fn=_fake_embed,
                        router_llm_fn=_fake_router(answer_seeking=True))
    result = await agent.ainvoke({
        "message": "just tell me how merge sort works", "class_id": cls.id,
        "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    system = result["messages_payload"][0]["content"]
    assert "ANSWER GUARDRAIL" in system
    assert "Do NOT give the final answer" in system


@pytest.mark.asyncio
async def test_effort_drives_coaching_strategy(pg_session):
    student, cls, lab = await _seed_lab(pg_session)
    # Teacher-pinned low profile → the low coaching strategy is injected (the
    # router's effort still folds into the EMA underneath).
    pg_session.add(LearnerProfile(
        id=uuid.uuid4(), user_id=student.id, lab_id=lab.id,
        effort_ema=0.1, samples=5, coaching_level=CoachingLevel.low, teacher_override=True,
    ))
    await pg_session.commit()

    agent = build_agent(pg_session, embed_fn=_fake_embed,
                        router_llm_fn=_fake_router(effort="low"))
    result = await agent.ainvoke({
        "message": "merge sort", "class_id": cls.id, "lab_id": lab.id,
        "user_id": student.id, "history": [],
    })

    system = result["messages_payload"][0]["content"]
    assert "[COACHING]" in system
    assert "low-context" in system


@pytest.mark.asyncio
async def test_direct_route_also_coaches(pg_session):
    """`direct` skips retrieval, but the student is still being coached."""
    student, cls, lab = await _seed_lab(pg_session)
    pg_session.add(LearnerProfile(
        id=uuid.uuid4(), user_id=student.id, lab_id=lab.id,
        effort_ema=0.1, samples=5, coaching_level=CoachingLevel.low, teacher_override=True,
    ))
    await pg_session.commit()

    agent = build_agent(pg_session, embed_fn=_fake_embed,
                        router_llm_fn=_fake_router(route="direct", effort="low"))
    result = await agent.ainvoke({
        "message": "hm", "class_id": cls.id, "lab_id": lab.id,
        "user_id": student.id, "history": [],
    })

    assert result["route"] == "direct"
    assert "[COACHING]" in result["messages_payload"][0]["content"]


@pytest.mark.asyncio
async def test_test_drive_does_not_update_learner_profile(pg_session):
    """A teacher test-drive (is_test) never touches student coaching data —
    record_effort is skipped, so no LearnerProfile is created or updated."""
    student, cls, lab = await _seed_lab(pg_session)

    agent = build_agent(pg_session, embed_fn=_fake_embed,
                        router_llm_fn=_fake_router(effort="low"))
    await agent.ainvoke({
        "message": "merge sort", "class_id": cls.id, "lab_id": lab.id,
        "user_id": student.id, "history": [], "is_test": True,
    })

    profile = (await pg_session.execute(
        select(LearnerProfile).where(
            LearnerProfile.user_id == student.id, LearnerProfile.lab_id == lab.id,
        )
    )).scalar_one_or_none()
    assert profile is None
