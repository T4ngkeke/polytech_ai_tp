"""
test_teacher.py — Tests for teacher endpoints (v7).
"""

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    Class, ClassStudent, Lab, Message, Rule, RuleLevel,
    SenderType, Session, UsageStat, User, UserRole,
)
from tests.conftest import make_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed_data(db_session: AsyncSession) -> dict:
    admin = make_user(role=UserRole.admin, username="admin1")
    teacher = make_user(role=UserRole.teacher, username="teacher1")
    student_a = make_user(role=UserRole.student, username="student_a")
    student_b = make_user(role=UserRole.student, username="student_b")
    deleted = make_user(role=UserRole.student, username="deleted_s", is_deleted=True)
    db_session.add_all([admin, teacher, student_a, student_b, deleted])
    await db_session.flush()

    cls = Class(name="Test Class", teacher_id=teacher.id, invite_code="ABC123")
    db_session.add(cls)
    await db_session.flush()

    lab = Lab(class_id=cls.id, name="Test Lab")
    db_session.add(lab)
    await db_session.flush()

    db_session.add_all([
        ClassStudent(class_id=cls.id, student_id=student_a.id),
        ClassStudent(class_id=cls.id, student_id=student_b.id),
    ])
    await db_session.flush()

    session = Session(user_id=student_a.id, lab_id=lab.id, title="Chat 1")
    db_session.add(session)
    await db_session.flush()

    m1 = Message(session_id=session.id, sender=SenderType.user, content="Hello",
                 total_tokens=10)
    m2 = Message(session_id=session.id, sender=SenderType.llm, content="Hi!",
                 total_tokens=15)
    db_session.add_all([m1, m2])

    usage = UsageStat(user_id=student_a.id, date=date.today(),
                      tokens_used=1234, request_count=5)
    db_session.add(usage)

    rule = Rule(level=RuleLevel.class_, target_id=cls.id,
                rules_text="Be formal.", is_active=True)
    db_session.add(rule)

    await db_session.commit()
    for obj in [admin, teacher, student_a, student_b, cls, lab, session, rule]:
        await db_session.refresh(obj)
    return {
        "admin": admin, "teacher": teacher,
        "student_a": student_a, "student_b": student_b,
        "class": cls, "lab": lab, "session": session, "rule": rule,
    }


@pytest_asyncio.fixture
async def teacher_client(db_session, seed_data):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(seed_data["teacher"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def student_client(db_session, seed_data):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(seed_data["student_a"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


# ===================================================================
# Original endpoints
# ===================================================================

class TestCreateClass:
    async def test_teacher_creates_class(self, teacher_client, seed_data):
        resp = await teacher_client.post("/api/teacher/classes", json={"name": "New Class"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "New Class"
        assert len(body["invite_code"]) == 6

    async def test_student_cannot_create_403(self, student_client):
        resp = await student_client.post("/api/teacher/classes", json={"name": "Bad"})
        assert resp.status_code == 403

    async def test_missing_name_422(self, teacher_client):
        resp = await teacher_client.post("/api/teacher/classes", json={})
        assert resp.status_code == 422


class TestListClasses:
    async def test_teacher_sees_own_classes(self, teacher_client, seed_data):
        resp = await teacher_client.get("/api/teacher/classes")
        assert resp.status_code == 200
        assert any(c["name"] == "Test Class" for c in resp.json())

    async def test_soft_deleted_classes_hidden(self, teacher_client, seed_data, db_session):
        """After soft-deleting, class disappears from list."""
        cls = seed_data["class"]
        cls.is_deleted = True
        db_session.add(cls)
        await db_session.flush()
        resp = await teacher_client.get("/api/teacher/classes")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Test Class" not in names

    async def test_student_rejected_403(self, student_client):
        resp = await student_client.get("/api/teacher/classes")
        assert resp.status_code == 403


class TestCreateLab:
    async def test_teacher_creates_lab(self, teacher_client, seed_data):
        resp = await teacher_client.post(
            f"/api/teacher/classes/{seed_data['class'].id}/labs",
            json={"name": "New Lab"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "New Lab"

    async def test_nonexistent_class_404(self, teacher_client):
        resp = await teacher_client.post(
            f"/api/teacher/classes/{uuid.uuid4()}/labs", json={"name": "X"})
        assert resp.status_code == 404

    async def test_student_cannot_create_403(self, student_client, seed_data):
        resp = await student_client.post(
            f"/api/teacher/classes/{seed_data['class'].id}/labs", json={"name": "X"})
        assert resp.status_code == 403


class TestListLabs:
    async def test_teacher_lists_labs(self, teacher_client, seed_data):
        resp = await teacher_client.get(
            f"/api/teacher/classes/{seed_data['class'].id}/labs")
        assert resp.status_code == 200
        assert any(l["name"] == "Test Lab" for l in resp.json())

    async def test_soft_deleted_labs_hidden(self, teacher_client, seed_data, db_session):
        lab = seed_data["lab"]
        lab.is_deleted = True
        db_session.add(lab)
        await db_session.flush()
        resp = await teacher_client.get(
            f"/api/teacher/classes/{seed_data['class'].id}/labs")
        assert resp.status_code == 200
        names = [l["name"] for l in resp.json()]
        assert "Test Lab" not in names

    async def test_nonexistent_class_404(self, teacher_client):
        resp = await teacher_client.get(
            f"/api/teacher/classes/{uuid.uuid4()}/labs")
        assert resp.status_code == 404


class TestUpsertRule:
    async def test_create_new_rule(self, teacher_client, seed_data):
        resp = await teacher_client.put("/api/teacher/rules", json={
            "level": "lab", "target_id": str(seed_data["lab"].id),
            "rules_text": "No code sharing.", "is_active": True,
        })
        assert resp.status_code == 200
        assert resp.json()["rules_text"] == "No code sharing."

    async def test_upsert_updates_existing(self, teacher_client, seed_data):
        rule = seed_data["rule"]
        resp = await teacher_client.put("/api/teacher/rules", json={
            "level": "class", "target_id": str(rule.target_id),
            "rules_text": "Updated.", "is_active": False,
        })
        assert resp.status_code == 200
        assert resp.json()["rules_text"] == "Updated."
        assert resp.json()["id"] == str(rule.id)

    async def test_student_cannot_upsert_403(self, student_client, seed_data):
        resp = await student_client.put("/api/teacher/rules", json={
            "level": "class", "target_id": str(seed_data["class"].id),
            "rules_text": "hack",
        })
        assert resp.status_code == 403

    async def test_missing_fields_422(self, teacher_client):
        resp = await teacher_client.put("/api/teacher/rules", json={})
        assert resp.status_code == 422


class TestListRules:
    async def test_list_all(self, teacher_client, seed_data):
        resp = await teacher_client.get("/api/teacher/rules")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_filter_by_level(self, teacher_client, seed_data):
        resp = await teacher_client.get("/api/teacher/rules?level=class")
        assert resp.status_code == 200
        assert all(r["level"] == "class" for r in resp.json())

    async def test_filter_by_target_id(self, teacher_client, seed_data):
        target = seed_data["class"].id
        resp = await teacher_client.get(f"/api/teacher/rules?target_id={target}")
        assert resp.status_code == 200
        assert all(r["target_id"] == str(target) for r in resp.json())


class TestChatHistory:
    async def test_no_filter(self, teacher_client, seed_data):
        resp = await teacher_client.get("/api/teacher/chat-history")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1
        assert "messages" in resp.json()[0]

    async def test_filter_by_student(self, teacher_client, seed_data):
        sid = seed_data["student_a"].id
        resp = await teacher_client.get(f"/api/teacher/chat-history?student_id={sid}")
        assert resp.status_code == 200
        assert all(s["user_id"] == str(sid) for s in resp.json())

    async def test_filter_by_lab(self, teacher_client, seed_data):
        lid = seed_data["lab"].id
        resp = await teacher_client.get(f"/api/teacher/chat-history?lab_id={lid}")
        assert resp.status_code == 200
        assert all(s["lab_id"] == str(lid) for s in resp.json())

    async def test_filter_by_class(self, teacher_client, seed_data):
        resp = await teacher_client.get(
            f"/api/teacher/chat-history?class_id={seed_data['class'].id}")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_filter_by_session(self, teacher_client, seed_data):
        resp = await teacher_client.get(
            f"/api/teacher/chat-history?session_id={seed_data['session'].id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_student_rejected_403(self, student_client):
        resp = await student_client.get("/api/teacher/chat-history")
        assert resp.status_code == 403

    async def test_nonexistent_class_empty(self, teacher_client):
        resp = await teacher_client.get(
            f"/api/teacher/chat-history?class_id={uuid.uuid4()}")
        assert resp.status_code == 200
        assert resp.json() == []


class TestListStudents:
    async def test_teacher_sees_active(self, teacher_client, seed_data):
        resp = await teacher_client.get("/api/teacher/students")
        assert resp.status_code == 200
        usernames = [s["username"] for s in resp.json()]
        assert "student_a" in usernames
        assert "deleted_s" not in usernames

    async def test_student_a_has_usage(self, teacher_client, seed_data):
        resp = await teacher_client.get("/api/teacher/students")
        a = next(s for s in resp.json() if s["username"] == "student_a")
        assert a["tokens_used_today"] == 1234

    async def test_student_rejected_403(self, student_client):
        resp = await student_client.get("/api/teacher/students")
        assert resp.status_code == 403


# ===================================================================
# New endpoints
# ===================================================================

class TestUnenrollStudent:
    async def test_unenroll_student(self, teacher_client, seed_data, db_session):
        cid = seed_data["class"].id
        sid = seed_data["student_b"].id
        resp = await teacher_client.delete(
            f"/api/teacher/classes/{cid}/students/{sid}")
        assert resp.status_code == 204
        r = await db_session.execute(select(ClassStudent).where(
            ClassStudent.class_id == cid, ClassStudent.student_id == sid))
        assert r.scalar_one_or_none() is None

    async def test_not_enrolled_404(self, teacher_client, seed_data):
        resp = await teacher_client.delete(
            f"/api/teacher/classes/{seed_data['class'].id}/students/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_wrong_class_404(self, teacher_client):
        resp = await teacher_client.delete(
            f"/api/teacher/classes/{uuid.uuid4()}/students/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_client, seed_data):
        resp = await student_client.delete(
            f"/api/teacher/classes/{seed_data['class'].id}/students/{seed_data['student_b'].id}")
        assert resp.status_code == 403


class TestResetInviteCode:
    async def test_generates_new_code(self, teacher_client, seed_data):
        old_code = seed_data["class"].invite_code
        resp = await teacher_client.post(
            f"/api/teacher/classes/{seed_data['class'].id}/reset-code")
        assert resp.status_code == 200
        new_code = resp.json()["invite_code"]
        assert len(new_code) == 6
        assert new_code != old_code

    async def test_wrong_class_404(self, teacher_client):
        resp = await teacher_client.post(
            f"/api/teacher/classes/{uuid.uuid4()}/reset-code")
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_client, seed_data):
        resp = await student_client.post(
            f"/api/teacher/classes/{seed_data['class'].id}/reset-code")
        assert resp.status_code == 403


class TestUpdateClass:
    async def test_updates_name(self, teacher_client, seed_data):
        resp = await teacher_client.put(
            f"/api/teacher/classes/{seed_data['class'].id}",
            json={"name": "Renamed Class"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed Class"

    async def test_wrong_class_404(self, teacher_client):
        resp = await teacher_client.put(
            f"/api/teacher/classes/{uuid.uuid4()}", json={"name": "X"})
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_client, seed_data):
        resp = await student_client.put(
            f"/api/teacher/classes/{seed_data['class'].id}", json={"name": "X"})
        assert resp.status_code == 403


class TestSoftDeleteClass:
    async def test_soft_deletes(self, teacher_client, seed_data, db_session):
        cid = seed_data["class"].id
        resp = await teacher_client.delete(f"/api/teacher/classes/{cid}")
        assert resp.status_code == 204
        await db_session.refresh(seed_data["class"])
        assert seed_data["class"].is_deleted is True

    async def test_already_deleted_404(self, teacher_client, seed_data, db_session):
        cls = seed_data["class"]
        cls.is_deleted = True
        db_session.add(cls)
        await db_session.flush()
        resp = await teacher_client.delete(f"/api/teacher/classes/{cls.id}")
        assert resp.status_code == 404

    async def test_wrong_class_404(self, teacher_client):
        resp = await teacher_client.delete(f"/api/teacher/classes/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestUpdateLab:
    async def test_updates_name(self, teacher_client, seed_data):
        resp = await teacher_client.put(
            f"/api/teacher/labs/{seed_data['lab'].id}",
            json={"name": "Renamed Lab"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed Lab"

    async def test_toggle_is_active(self, teacher_client, seed_data):
        resp = await teacher_client.put(
            f"/api/teacher/labs/{seed_data['lab'].id}",
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_nonexistent_lab_404(self, teacher_client):
        resp = await teacher_client.put(
            f"/api/teacher/labs/{uuid.uuid4()}", json={"name": "X"})
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_client, seed_data):
        resp = await student_client.put(
            f"/api/teacher/labs/{seed_data['lab'].id}", json={"name": "X"})
        assert resp.status_code == 403


class TestSoftDeleteLab:
    async def test_soft_deletes(self, teacher_client, seed_data, db_session):
        lid = seed_data["lab"].id
        resp = await teacher_client.delete(f"/api/teacher/labs/{lid}")
        assert resp.status_code == 204
        await db_session.refresh(seed_data["lab"])
        assert seed_data["lab"].is_deleted is True

    async def test_already_deleted_404(self, teacher_client, seed_data, db_session):
        lab = seed_data["lab"]
        lab.is_deleted = True
        db_session.add(lab)
        await db_session.flush()
        resp = await teacher_client.delete(f"/api/teacher/labs/{lab.id}")
        assert resp.status_code == 404

    async def test_nonexistent_404(self, teacher_client):
        resp = await teacher_client.delete(f"/api/teacher/labs/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestTeacherClassAnalytics:
    async def test_returns_analytics(self, teacher_client, seed_data):
        resp = await teacher_client.get(
            f"/api/teacher/analytics/classes/{seed_data['class'].id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["class_id"] == str(seed_data["class"].id)
        assert body["total_tokens"] >= 25  # 10 + 15 from messages
        assert len(body["labs"]) >= 1
        assert len(body["labs"][0]["students"]) >= 1

    async def test_wrong_class_404(self, teacher_client):
        resp = await teacher_client.get(
            f"/api/teacher/analytics/classes/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_client, seed_data):
        resp = await student_client.get(
            f"/api/teacher/analytics/classes/{seed_data['class'].id}")
        assert resp.status_code == 403


# ===================================================================
# [v7.2] Skill presets (library + apply-to-class)
# ===================================================================

class TestSkillPresets:
    async def test_create_list_update_delete_preset(self, teacher_client):
        created = await teacher_client.post("/api/teacher/skill-presets",
                                            json={"name": "Socratic", "content": "Guide."})
        assert created.status_code == 201
        pid = created.json()["id"]

        listed = await teacher_client.get("/api/teacher/skill-presets")
        assert [p["name"] for p in listed.json()] == ["Socratic"]

        updated = await teacher_client.put(f"/api/teacher/skill-presets/{pid}",
                                           json={"name": "Socratic v2", "content": "Hint only."})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Socratic v2"

        deleted = await teacher_client.delete(f"/api/teacher/skill-presets/{pid}")
        assert deleted.status_code == 204
        assert (await teacher_client.get("/api/teacher/skill-presets")).json() == []

    async def test_apply_preset_to_class_snapshots_rule(self, teacher_client, seed_data):
        created = await teacher_client.post("/api/teacher/skill-presets",
                                            json={"name": "P", "content": "BE SOCRATIC"})
        pid = created.json()["id"]
        class_id = seed_data["class"].id

        resp = await teacher_client.post(f"/api/teacher/classes/{class_id}/skill",
                                         json={"preset_id": pid})
        assert resp.status_code == 200

        rules = await teacher_client.get(
            f"/api/teacher/rules?level=class&target_id={class_id}")
        assert any(r["rules_text"] == "BE SOCRATIC" for r in rules.json())

    async def test_apply_raw_content_to_class(self, teacher_client, seed_data):
        class_id = seed_data["class"].id
        resp = await teacher_client.post(f"/api/teacher/classes/{class_id}/skill",
                                         json={"content": "AD HOC STYLE"})
        assert resp.status_code == 200
        rules = await teacher_client.get(
            f"/api/teacher/rules?level=class&target_id={class_id}")
        assert any(r["rules_text"] == "AD HOC STYLE" for r in rules.json())

    async def test_student_cannot_create_preset_403(self, student_client):
        resp = await student_client.post("/api/teacher/skill-presets",
                                         json={"name": "x", "content": "y"})
        assert resp.status_code == 403

    async def test_apply_to_unowned_class_403(self, teacher_client, db_session):
        # A class owned by a different teacher.
        other = make_user(role=UserRole.teacher, username="other_t")
        db_session.add(other)
        await db_session.flush()
        foreign = Class(name="Foreign", teacher_id=other.id, invite_code="ZZZ999")
        db_session.add(foreign)
        await db_session.commit()

        created = await teacher_client.post("/api/teacher/skill-presets",
                                            json={"name": "P", "content": "X"})
        resp = await teacher_client.post(
            f"/api/teacher/classes/{foreign.id}/skill",
            json={"preset_id": created.json()["id"]})
        assert resp.status_code == 403
