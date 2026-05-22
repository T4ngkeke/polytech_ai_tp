"""
test_student.py — Tests for student session management endpoints.
"""

import json
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import Message, SenderType, Session, User, UserRole


def _make_user(username: str, role: UserRole = UserRole.student) -> User:
    return User(
        id=uuid.uuid4(),
        username=username,
        hashed_password=hash_password("password1"),
        role=role,
        daily_token_quota=50_000,
    )


@pytest_asyncio.fixture
async def seed_student_data(db_session: AsyncSession) -> dict:
    student1 = _make_user("student1")
    student2 = _make_user("student2")
    db_session.add_all([student1, student2])
    await db_session.flush()

    # Create sessions for student1
    sess1 = Session(id=uuid.uuid4(), user_id=student1.id, title="Math Help")
    sess2 = Session(id=uuid.uuid4(), user_id=student1.id, title="Science Help")

    # Create a session for student2
    sess3 = Session(id=uuid.uuid4(), user_id=student2.id, title="History Help")

    db_session.add_all([sess1, sess2, sess3])
    await db_session.flush()

    # Add messages to sess1
    m1 = Message(session_id=sess1.id, sender=SenderType.user, content="What is 2+2?")
    m2 = Message(session_id=sess1.id, sender=SenderType.llm, content="2+2 = 4")
    db_session.add_all([m1, m2])

    await db_session.commit()
    for obj in [student1, student2, sess1, sess2, sess3]:
        await db_session.refresh(obj)

    return {
        "student1": student1,
        "student2": student2,
        "sess1": sess1,
        "sess2": sess2,
        "sess3": sess3,
    }


@pytest_asyncio.fixture
async def client_s1(db_session: AsyncSession, seed_student_data) -> AsyncClient:
    """Authenticated client for student1."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_student_data["student1"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_s2(db_session: AsyncSession, seed_student_data) -> AsyncClient:
    """Authenticated client for student2."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_student_data["student2"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ===================================================================
# POST /api/student/sessions
# ===================================================================


class TestCreateSession:
    async def test_create_session_returns_201(self, client_s1):
        resp = await client_s1.post(
            "/api/student/sessions",
            json={"title": "New Session"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "New Session"
        assert data["is_deleted"] is False
        assert "id" in data

    async def test_create_session_with_null_title(self, client_s1):
        resp = await client_s1.post(
            "/api/student/sessions",
            json={},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] is None

    async def test_unauthenticated_returns_403(self, db_session: AsyncSession):
        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/student/sessions", json={"title": "x"})
        app.dependency_overrides.clear()
        assert resp.status_code == 401


# ===================================================================
# GET /api/student/sessions
# ===================================================================


class TestListSessions:
    async def test_returns_only_own_sessions(self, client_s1, seed_student_data):
        resp = await client_s1.get("/api/student/sessions")
        assert resp.status_code == 200
        data = resp.json()
        # student1 has 2 sessions
        assert len(data) == 2
        ids = {item["id"] for item in data}
        assert str(seed_student_data["sess1"].id) in ids
        assert str(seed_student_data["sess2"].id) in ids
        # student2's session should NOT be present
        assert str(seed_student_data["sess3"].id) not in ids

    async def test_student2_sees_only_own(self, client_s2, seed_student_data):
        resp = await client_s2.get("/api/student/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == str(seed_student_data["sess3"].id)


# ===================================================================
# GET /api/student/sessions/{session_id}
# ===================================================================


class TestGetSession:
    async def test_get_own_session_with_messages(self, client_s1, seed_student_data):
        sess1 = seed_student_data["sess1"]
        resp = await client_s1.get(f"/api/student/sessions/{sess1.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(sess1.id)
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "What is 2+2?"
        assert data["messages"][1]["content"] == "2+2 = 4"

    async def test_idor_returns_403(self, client_s1, seed_student_data):
        """Student1 tries to access student2's session → 403."""
        sess3 = seed_student_data["sess3"]
        resp = await client_s1.get(f"/api/student/sessions/{sess3.id}")
        assert resp.status_code == 403

    async def test_nonexistent_session_returns_404(self, client_s1):
        resp = await client_s1.get(f"/api/student/sessions/{uuid.uuid4()}")
        assert resp.status_code == 404
