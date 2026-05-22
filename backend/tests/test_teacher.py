"""
test_teacher.py — TDD tests for backend/app/routers/teacher.py

Written BEFORE the implementation (RED phase).

Coverage
--------
- GET  /api/teacher/students               — list active students + today's usage
- GET  /api/teacher/sessions/{student_id}  — fetch a student's chat history
- POST /api/teacher/rules                  — create a TeacherRule
- PUT  /api/teacher/rules/{rule_id}/toggle — toggle is_active
- RBAC enforcement: student → 403, teacher → allowed, admin → allowed
"""

import json
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    Message,
    SenderType,
    Session,
    TeacherRule,
    UsageStat,
    User,
    UserRole,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    role: UserRole = UserRole.student,
    is_deleted: bool = False,
    username: str | None = None,
) -> User:
    return User(
        id=uuid.uuid4(),
        username=username or f"user_{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("password1"),
        role=role,
        daily_token_quota=50_000,
        is_deleted=is_deleted,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_data(db_session: AsyncSession) -> dict:
    """
    Seed the DB with:
    - 1 admin, 1 teacher, 2 active students, 1 deleted student
    - Usage stats for student_a (today) and student_b (no usage today)
    - 1 session + 2 messages for student_a
    - 1 TeacherRule targeting student_a
    """
    admin = _make_user(role=UserRole.admin, username="admin1")
    teacher = _make_user(role=UserRole.teacher, username="teacher1")
    student_a = _make_user(role=UserRole.student, username="student_a")
    student_b = _make_user(role=UserRole.student, username="student_b")
    deleted = _make_user(role=UserRole.student, username="deleted_s", is_deleted=True)

    db_session.add_all([admin, teacher, student_a, student_b, deleted])
    await db_session.flush()

    # Usage stats for student_a today
    today = date.today()
    usage = UsageStat(
        id=uuid.uuid4(),
        user_id=student_a.id,
        date=today,
        tokens_used=1234,
        request_count=5,
    )
    db_session.add(usage)

    # Session + messages for student_a
    session = Session(
        id=uuid.uuid4(),
        user_id=student_a.id,
        title="Test Chat",
        is_deleted=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.flush()

    msg1 = Message(
        id=uuid.uuid4(),
        session_id=session.id,
        sender=SenderType.user,
        content="Hello",
        created_at=datetime.now(timezone.utc),
    )
    msg2 = Message(
        id=uuid.uuid4(),
        session_id=session.id,
        sender=SenderType.llm,
        content="Hi there!",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([msg1, msg2])

    # TeacherRule targeting student_a
    rule = TeacherRule(
        id=uuid.uuid4(),
        teacher_id=teacher.id,
        student_id=student_a.id,
        rules_json=json.dumps({"instruction": "Be concise"}),
        is_active=True,
    )
    db_session.add(rule)
    await db_session.commit()

    for obj in [admin, teacher, student_a, student_b, deleted, session, rule]:
        await db_session.refresh(obj)

    return {
        "admin": admin,
        "teacher": teacher,
        "student_a": student_a,
        "student_b": student_b,
        "deleted": deleted,
        "session": session,
        "rule": rule,
    }


@pytest_asyncio.fixture
async def teacher_client(db_session: AsyncSession, seed_data) -> AsyncClient:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_data["teacher"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client(db_session: AsyncSession, seed_data) -> AsyncClient:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_data["admin"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def student_client(db_session: AsyncSession, seed_data) -> AsyncClient:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(user_id=seed_data["student_a"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ===================================================================
# 1. GET /api/teacher/students
# ===================================================================


class TestListStudents:
    async def test_teacher_gets_active_students_with_usage(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.get("/api/teacher/students")
        assert resp.status_code == 200
        data = resp.json()
        # Should return only active students (student_a, student_b)
        usernames = [s["username"] for s in data]
        assert "student_a" in usernames
        assert "student_b" in usernames
        assert "deleted_s" not in usernames
        assert "teacher1" not in usernames
        assert "admin1" not in usernames

    async def test_student_a_has_todays_usage(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.get("/api/teacher/students")
        data = resp.json()
        a = next(s for s in data if s["username"] == "student_a")
        assert a["tokens_used_today"] == 1234
        assert a["request_count_today"] == 5

    async def test_student_b_has_zero_usage(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.get("/api/teacher/students")
        data = resp.json()
        b = next(s for s in data if s["username"] == "student_b")
        assert b["tokens_used_today"] == 0
        assert b["request_count_today"] == 0

    async def test_admin_can_also_access(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.get("/api/teacher/students")
        assert resp.status_code == 200

    async def test_student_is_rejected_with_403(
        self, student_client: AsyncClient
    ):
        resp = await student_client.get("/api/teacher/students")
        assert resp.status_code == 403


# ===================================================================
# 2. GET /api/teacher/sessions/{student_id}
# ===================================================================


class TestGetStudentSessions:
    async def test_teacher_gets_student_sessions_with_messages(
        self, teacher_client: AsyncClient, seed_data
    ):
        student_a = seed_data["student_a"]
        resp = await teacher_client.get(f"/api/teacher/sessions/{student_a.id}")
        assert resp.status_code == 200
        data = resp.json()
        # Should have at least 1 session
        assert len(data) >= 1
        sess = data[0]
        assert "id" in sess
        assert "messages" in sess
        assert len(sess["messages"]) == 2

    async def test_message_fields_are_present(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.get(
            f"/api/teacher/sessions/{seed_data['student_a'].id}"
        )
        msg = resp.json()[0]["messages"][0]
        assert "sender" in msg
        assert "content" in msg
        assert "created_at" in msg

    async def test_student_with_no_sessions_returns_empty_list(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.get(
            f"/api/teacher/sessions/{seed_data['student_b'].id}"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_nonexistent_student_returns_404(
        self, teacher_client: AsyncClient
    ):
        resp = await teacher_client.get(
            f"/api/teacher/sessions/{uuid.uuid4()}"
        )
        assert resp.status_code == 404

    async def test_student_cannot_view_other_sessions(
        self, student_client: AsyncClient, seed_data
    ):
        resp = await student_client.get(
            f"/api/teacher/sessions/{seed_data['student_b'].id}"
        )
        assert resp.status_code == 403


# ===================================================================
# 3. POST /api/teacher/rules
# ===================================================================


class TestCreateRule:
    async def test_teacher_creates_rule_for_student(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.post(
            "/api/teacher/rules",
            json={
                "student_id": str(seed_data["student_a"].id),
                "rules_json": {"instruction": "Always use French"},
                "is_active": True,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["teacher_id"] == str(seed_data["teacher"].id)
        assert body["student_id"] == str(seed_data["student_a"].id)
        assert body["is_active"] is True
        assert "id" in body

    async def test_teacher_creates_global_rule_without_student_id(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.post(
            "/api/teacher/rules",
            json={
                "rules_json": {"instruction": "No code execution"},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["student_id"] is None

    async def test_student_cannot_create_rule(
        self, student_client: AsyncClient
    ):
        resp = await student_client.post(
            "/api/teacher/rules",
            json={"rules_json": {"instruction": "hax"}},
        )
        assert resp.status_code == 403

    async def test_missing_rules_json_returns_422(
        self, teacher_client: AsyncClient
    ):
        resp = await teacher_client.post("/api/teacher/rules", json={})
        assert resp.status_code == 422


# ===================================================================
# 4. PUT /api/teacher/rules/{rule_id}/toggle
# ===================================================================


class TestToggleRule:
    async def test_teacher_toggles_rule_active_to_inactive(
        self, teacher_client: AsyncClient, seed_data
    ):
        rule = seed_data["rule"]
        assert rule.is_active is True

        resp = await teacher_client.put(f"/api/teacher/rules/{rule.id}/toggle")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(rule.id)
        assert body["is_active"] is False

    async def test_double_toggle_restores_original(
        self, teacher_client: AsyncClient, seed_data
    ):
        rule = seed_data["rule"]
        # Toggle off
        await teacher_client.put(f"/api/teacher/rules/{rule.id}/toggle")
        # Toggle on
        resp = await teacher_client.put(f"/api/teacher/rules/{rule.id}/toggle")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

    async def test_nonexistent_rule_returns_404(
        self, teacher_client: AsyncClient
    ):
        resp = await teacher_client.put(
            f"/api/teacher/rules/{uuid.uuid4()}/toggle"
        )
        assert resp.status_code == 404

    async def test_student_cannot_toggle_rule(
        self, student_client: AsyncClient, seed_data
    ):
        resp = await student_client.put(
            f"/api/teacher/rules/{seed_data['rule'].id}/toggle"
        )
        assert resp.status_code == 403


# ===================================================================
# 5. GET /api/teacher/rules
# ===================================================================


class TestListRules:
    async def test_teacher_sees_own_rules(
        self, teacher_client: AsyncClient, seed_data
    ):
        resp = await teacher_client.get("/api/teacher/rules")
        assert resp.status_code == 200
        data = resp.json()
        # The seed creates 1 rule owned by this teacher
        assert len(data) >= 1
        assert all(r["teacher_id"] == str(seed_data["teacher"].id) for r in data)

    async def test_other_teachers_rules_not_visible(
        self, db_session: AsyncSession, seed_data
    ):
        """A second teacher should NOT see the first teacher's rules."""
        teacher2 = _make_user(role=UserRole.teacher, username="teacher2")
        db_session.add(teacher2)
        await db_session.commit()
        await db_session.refresh(teacher2)

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        token = create_access_token(user_id=teacher2.id)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            resp = await client.get("/api/teacher/rules")
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_student_cannot_list_rules(
        self, student_client: AsyncClient
    ):
        resp = await student_client.get("/api/teacher/rules")
        assert resp.status_code == 403

