"""
test_agent_tutor.py — v7 tutoring wired into the agent graph.

Answer-seeking → Socratic guardrail in the system prompt. Coaching level (from
the smoothed profile) → a neutral coaching strategy. Runs on SQLite; the test
messages route to agentic_search (regex) or direct (kNN), neither of which needs
pgvector.
"""

import uuid

import pytest

from backend.app.agent.graph import build_agent
from backend.app.agent.router import INTENT_EXEMPLARS
from backend.app.models import Class, CoachingLevel, Lab, LearnerProfile, UserRole
from backend.tests.conftest import make_user

_RAG_EXEMPLARS = set(INTENT_EXEMPLARS["rag"])


async def _fake_embed(texts):
    # Content-aware: rag exemplars on one axis; direct exemplars + benign queries
    # on the other, so a benign message deterministically routes to `direct`.
    return [[1.0, 0.0] if t in _RAG_EXEMPLARS else [0.0, 1.0] for t in texts]


async def _seed(session):
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
    await session.commit()
    return student, cls, lab


@pytest.mark.asyncio
async def test_answer_seeking_injects_socratic_guardrail(db_session):
    student, cls, lab = await _seed(db_session)
    agent = build_agent(db_session, embed_fn=_fake_embed)

    result = await agent.ainvoke({
        "message": "just give me the answer to exercise 2",
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["answer_seeking"] is True
    system = result["messages_payload"][0]["content"]
    assert "ANSWER GUARDRAIL" in system
    assert "Do NOT give the final answer" in system


@pytest.mark.asyncio
async def test_low_coaching_level_injects_strategy(db_session):
    student, cls, lab = await _seed(db_session)
    # Teacher-pinned low profile → coaching strategy should be injected.
    db_session.add(LearnerProfile(
        id=uuid.uuid4(), user_id=student.id, lab_id=lab.id,
        effort_ema=0.1, samples=5, coaching_level=CoachingLevel.low, teacher_override=True,
    ))
    await db_session.commit()

    agent = build_agent(db_session, embed_fn=_fake_embed)
    result = await agent.ainvoke({
        "message": "thanks, that helped",  # benign → direct route, not answer-seeking
        "class_id": cls.id, "lab_id": lab.id, "user_id": student.id, "history": [],
    })

    assert result["answer_seeking"] is False
    system = result["messages_payload"][0]["content"]
    assert "[COACHING]" in system
    assert "low-context" in system
