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
                         lab_id=lab.id, chunk_index=0, content=body,
                         embedding=_unit(at), page_no=1))
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
