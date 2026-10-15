"""
test_chat.py — Tests for POST /api/chat/stream (v7).

Coverage
--------
- Gate checks: session ownership, class membership, quota enforcement
- The research agent is the only chat path (its answers are tested in
  test_m4d_chat_pg.py, with the teacher's rules in test_direct_mode.py)
"""

import uuid
from datetime import date
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    Class,
    ClassStudent,
    Lab,
    Message,
    Rule,
    RuleLevel,
    SenderType,
    Session,
    SystemConfig,
    UsageStat,
    UserRole,
)
from tests.conftest import make_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_chat(db_session: AsyncSession) -> dict:
    """
    Seed:
    - teacher, student1 (enrolled), student2 (NOT enrolled)
    - 1 Class, 1 Lab, student1 enrolled
    - 1 Session for student1 in the lab (with 2 old messages)
    - 1 Session for student1 without lab (legacy)
    - SystemConfig rows for LLM
    - 3-tier rules: class, lab, student
    """
    teacher = make_user(role=UserRole.teacher, username="teacher")
    student1 = make_user(role=UserRole.student, username="student1", quota=100)
    student2 = make_user(role=UserRole.student, username="student2", quota=100)

    db_session.add_all([teacher, student1, student2])
    await db_session.flush()

    cls = Class(name="Chat Class", teacher_id=teacher.id, invite_code="CHAT01")
    db_session.add(cls)
    await db_session.flush()

    lab = Lab(class_id=cls.id, name="Chat Lab")
    db_session.add(lab)
    await db_session.flush()

    # Enroll student1
    db_session.add(ClassStudent(class_id=cls.id, student_id=student1.id))
    await db_session.flush()

    # Session with lab
    sess1 = Session(user_id=student1.id, lab_id=lab.id, title="Lab Chat")
    # Session without lab (legacy)
    sess_legacy = Session(user_id=student1.id, lab_id=None, title="Legacy Chat")
    # Session for student2 (not enrolled)
    sess2 = Session(user_id=student2.id, lab_id=lab.id, title="Unauthorized")
    db_session.add_all([sess1, sess_legacy, sess2])
    await db_session.flush()

    # Old messages in sess1
    m1 = Message(session_id=sess1.id, sender=SenderType.user, content="hi")
    m2 = Message(session_id=sess1.id, sender=SenderType.llm, content="hello")
    db_session.add_all([m1, m2])

    # SystemConfig
    configs = [
        SystemConfig(key="LLM_BASE_URL", value="http://test:11434/v1"),
        SystemConfig(key="LLM_API_KEY", value="test-key"),
        SystemConfig(key="LLM_MODEL", value="test-model"),
    ]
    db_session.add_all(configs)

    # Three-tier rules
    rule_class = Rule(level=RuleLevel.class_, target_id=cls.id, rules_text="Class: Be educational.", is_active=True)
    rule_lab = Rule(level=RuleLevel.lab, target_id=lab.id, rules_text="Lab: Focus on physics.", is_active=True)
    rule_student = Rule(level=RuleLevel.student, target_id=student1.id, rules_text="Student: Speak French.", is_active=True)
    db_session.add_all([rule_class, rule_lab, rule_student])

    await db_session.commit()
    for obj in [teacher, student1, student2, cls, lab, sess1, sess_legacy, sess2]:
        await db_session.refresh(obj)

    return {
        "teacher": teacher, "student1": student1, "student2": student2,
        "class": cls, "lab": lab,
        "sess1": sess1, "sess_legacy": sess_legacy, "sess2": sess2,
        "rule_class": rule_class, "rule_lab": rule_lab, "rule_student": rule_student,
    }


@pytest_asyncio.fixture
async def client1(db_session: AsyncSession, seed_chat) -> AsyncClient:
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(seed_chat["student1"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client2(db_session: AsyncSession, seed_chat) -> AsyncClient:
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(seed_chat["student2"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ===================================================================
# 1. Gate checks
# ===================================================================


class TestGateChecks:
    async def test_unauthenticated_returns_401(self, db_session):
        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/chat/stream", json={
                "session_id": str(uuid.uuid4()), "message": "hi",
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 401

    async def test_session_not_found_returns_404(self, client1):
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(uuid.uuid4()), "message": "hi",
        })
        assert resp.status_code == 404

    async def test_idor_session_returns_404(self, client1, seed_chat):
        """student1 tries to use student2's session."""
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess2"].id), "message": "hi",
        })
        assert resp.status_code == 404

    async def test_non_member_rejected_403(self, client2, seed_chat):
        """student2 (not enrolled) tries to chat in a lab session."""
        resp = await client2.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess2"].id), "message": "hi",
        })
        assert resp.status_code == 403

    async def test_owner_test_session_bypasses_membership(self, db_session, seed_chat):
        """[v8.0 §11A] The owning teacher's is_test session bypasses the student
        membership check — they aren't enrolled in their own class, but a
        test-drive is theirs to open."""
        teacher = seed_chat["teacher"]
        test_sess = Session(user_id=teacher.id, lab_id=seed_chat["lab"].id,
                            title="Test drive", is_test=True)
        db_session.add(test_sess)
        await db_session.commit()

        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        token = create_access_token(teacher.id)
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport, base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as c:
                resp = await c.post("/api/chat/stream", json={
                    "session_id": str(test_sess.id), "message": "hi",
                })
            # Not 403: past the membership check (the agent is off in this test).
            assert resp.status_code == 503 and resp.json()["detail"] == "chat_not_enabled"
        finally:
            app.dependency_overrides.clear()

    async def test_quota_exceeded_returns_429(self, client1, seed_chat, db_session):
        # Max out the quota
        usage = UsageStat(user_id=seed_chat["student1"].id, date=date.today(), tokens_used=100)
        db_session.add(usage)
        await db_session.commit()

        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 429

    async def test_locked_lab_rejected_403(self, client1, seed_chat, db_session):
        # [v7.2 fix] README §6: a locked (is_active=False) lab must reject chat.
        lab = seed_chat["lab"]
        lab.is_active = False
        db_session.add(lab)
        await db_session.commit()

        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 403

    async def test_deleted_lab_rejected_403(self, client1, seed_chat, db_session):
        lab = seed_chat["lab"]
        lab.is_deleted = True
        db_session.add(lab)
        await db_session.commit()

        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 403


# ===================================================================
# 2. Three-tier rule injection


# ===================================================================
# The research agent is the only chat path
# ===================================================================


class TestResearchAgentOnly:
    async def test_chat_needs_the_research_agent(self, client1, seed_chat):
        """With AGENTIC_SEARCH_ENABLED off there is no other path: a clear 503."""
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi"})
        assert resp.status_code == 503 and resp.json()["detail"] == "chat_not_enabled"

    async def test_session_without_lab_is_refused(self, client1, seed_chat, monkeypatch):
        from backend.app.config import settings
        monkeypatch.setattr(settings, "AGENTIC_SEARCH_ENABLED", True)
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess_legacy"].id), "message": "hi"})
        assert resp.status_code == 400 and resp.json()["detail"] == "session_without_lab"
