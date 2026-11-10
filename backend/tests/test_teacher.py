"""
test_teacher.py — Tests for teacher endpoints.
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
    Class, ClassStudent, Lab, Message, RuleItem,
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
                 total_tokens=15, billed_tokens=9)
    db_session.add_all([m1, m2])

    usage = UsageStat(user_id=student_a.id, date=date.today(),
                      tokens_used=1234, request_count=5)
    db_session.add(usage)

    rule = RuleItem(level="class", target_id=cls.id, class_id=cls.id,
                    text="Be formal.", is_active=True)
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
        assert body["exam_mode"] is True        # a new class starts in exam mode

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


class TestRuleItems:
    async def test_add_edit_switch_off_and_delete(self, teacher_client, seed_data):
        lab = seed_data["lab"]
        url = "/api/teacher/rule-items"
        first = await teacher_client.post(url, json={"level": "lab", "target_id": str(lab.id), "text": "No code."})
        second = await teacher_client.post(url, json={"level": "lab", "target_id": str(lab.id), "text": "Be short."})
        assert first.status_code == second.status_code == 201
        assert first.json()["class_id"] == str(seed_data["class"].id)      # set by the server
        listed = (await teacher_client.get(f"{url}?level=lab&target_id={lab.id}")).json()
        assert [r["text"] for r in listed] == ["No code.", "Be short."]
        rid = first.json()["id"]
        edited = await teacher_client.patch(f"{url}/{rid}", json={"text": "No full code."})
        off = await teacher_client.patch(f"{url}/{rid}", json={"is_active": False})
        assert edited.json()["text"] == "No full code." and off.json()["is_active"] is False
        assert (await teacher_client.patch(f"{url}/{rid}", json={"text": "   "})).status_code == 422
        assert (await teacher_client.delete(f"{url}/{rid}")).status_code == 204
        listed = (await teacher_client.get(f"{url}?level=lab&target_id={lab.id}")).json()
        assert [r["text"] for r in listed] == ["Be short."]

    async def test_blank_or_missing_text_422(self, teacher_client, seed_data):
        url = "/api/teacher/rule-items"
        target = str(seed_data["class"].id)
        assert (await teacher_client.post(url, json={"level": "class", "target_id": target, "text": "  "})).status_code == 422
        assert (await teacher_client.post(url, json={})).status_code == 422

    async def test_student_rule_belongs_to_one_class(self, teacher_client, seed_data):
        url = "/api/teacher/rule-items"
        student, cls = seed_data["student_a"], seed_data["class"]
        no_class = await teacher_client.post(url, json={"level": "student", "target_id": str(student.id), "text": "x"})
        assert no_class.status_code == 403
        ok = await teacher_client.post(url, json={"level": "student", "target_id": str(student.id),
                                                  "class_id": str(cls.id), "text": "Speak slowly."})
        assert ok.status_code == 201
        listed = await teacher_client.get(f"{url}?level=student&target_id={student.id}&class_id={cls.id}")
        assert [r["text"] for r in listed.json()] == ["Speak slowly."]

    async def test_student_cannot_use_rules_403(self, student_client, seed_data):
        resp = await student_client.post("/api/teacher/rule-items", json={
            "level": "class", "target_id": str(seed_data["class"].id), "text": "hack"})
        assert resp.status_code == 403

    async def test_foreign_class_and_lab_403_and_foreign_item_404(self, teacher_client, db_session):
        # Rules go into the prompt: a teacher must not touch another teacher's class.
        other = make_user(role=UserRole.teacher, username="other_t3")
        db_session.add(other)
        await db_session.flush()
        fcls = Class(name="Foreign", teacher_id=other.id, invite_code="FRGN02")
        db_session.add(fcls)
        await db_session.flush()
        flab = Lab(class_id=fcls.id, name="Foreign Lab")
        item = RuleItem(level="class", target_id=fcls.id, class_id=fcls.id, text="theirs")
        db_session.add_all([flab, item])
        await db_session.commit()
        url = "/api/teacher/rule-items"
        for level, target in (("class", fcls.id), ("lab", flab.id)):
            assert (await teacher_client.post(url, json={"level": level, "target_id": str(target),
                                                         "text": "hijack"})).status_code == 403
            assert (await teacher_client.get(f"{url}?level={level}&target_id={target}")).status_code == 403
        assert (await teacher_client.patch(f"{url}/{item.id}", json={"text": "hijack"})).status_code == 404
        assert (await teacher_client.delete(f"{url}/{item.id}")).status_code == 404

    async def test_at_most_thirty_rules(self, teacher_client, seed_data):
        url = "/api/teacher/rule-items"
        lab = str(seed_data["lab"].id)
        for n in range(30):
            assert (await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": f"r{n}"})).status_code == 201
        resp = await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": "one more"})
        assert resp.status_code == 409 and resp.json()["detail"] == "too_many_rules"

    async def test_built_in_presets(self, teacher_client):
        presets = (await teacher_client.get("/api/teacher/rule-presets")).json()
        assert len(presets) >= 8 and all(p["key"] and p["name"] and p["text"] for p in presets)

    async def test_a_rule_keeps_where_it_came_from(self, teacher_client, seed_data, db_session):
        url, lab = "/api/teacher/rule-items", str(seed_data["lab"].id)
        added = await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": "Hints only.",
                                                     "source": "b:hints"})
        assert added.status_code == 201 and added.json()["source"] == "b:hints"
        for bad in ("b:nope", "x:hints", f"m:{uuid.uuid4()}", "m:not-a-uuid"):
            resp = await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": "x", "source": bad})
            assert resp.status_code == 422, bad
        # PATCH without source keeps it; source null unlinks it.
        rid = added.json()["id"]
        assert (await teacher_client.patch(f"{url}/{rid}", json={"is_active": False})).json()["source"] == "b:hints"
        assert (await teacher_client.patch(f"{url}/{rid}", json={"source": None})).json()["source"] is None

    async def test_an_undone_delete_goes_back_to_its_place(self, teacher_client, seed_data):
        url, lab = "/api/teacher/rule-items", str(seed_data["lab"].id)
        ids = [(await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": t})).json()["id"]
               for t in ("one", "two", "three")]
        assert (await teacher_client.delete(f"{url}/{ids[1]}")).status_code == 204
        back = await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": "two", "position": 1})
        assert back.status_code == 201
        listed = (await teacher_client.get(f"{url}?level=lab&target_id={lab}")).json()
        assert [r["text"] for r in listed] == ["one", "two", "three"]

    async def test_saved_rules_stay_linked_to_the_lists(self, teacher_client, seed_data):
        url, lab = "/api/teacher/rule-items", str(seed_data["lab"].id)
        cls = str(seed_data["class"].id)
        preset = (await teacher_client.post("/api/teacher/skill-presets",
                                            json={"name": "C only", "content": "Use C."})).json()
        source = f"m:{preset['id']}"
        in_lab = (await teacher_client.post(url, json={"level": "lab", "target_id": lab, "text": "Use C.",
                                                       "source": source})).json()
        in_class = (await teacher_client.post(url, json={"level": "class", "target_id": cls, "text": "Use C.",
                                                         "source": source})).json()
        # Edited in the class list: that copy keeps its own text.
        await teacher_client.patch(f"{url}/{in_class['id']}", json={"text": "Use C99."})
        await teacher_client.put(f"/api/teacher/skill-presets/{preset['id']}", json={"name": "C only", "content": "Use C11."})
        lab_rules = (await teacher_client.get(f"{url}?level=lab&target_id={lab}")).json()
        class_rules = (await teacher_client.get(f"{url}?level=class&target_id={cls}")).json()
        assert [r["text"] for r in lab_rules if r["id"] == in_lab["id"]] == ["Use C11."]
        assert [r["text"] for r in class_rules if r["id"] == in_class["id"]] == ["Use C99."]
        # Deleting the saved rule keeps the rules, unlinked.
        assert (await teacher_client.delete(f"/api/teacher/skill-presets/{preset['id']}")).status_code == 204
        lab_rules = (await teacher_client.get(f"{url}?level=lab&target_id={lab}")).json()
        assert [(r["text"], r["source"]) for r in lab_rules if r["id"] == in_lab["id"]] == [("Use C11.", None)]


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

    async def test_teacher_cannot_update_foreign_lab_403(self, teacher_client, db_session):
        """The teacher route still enforces ownership — only the
        new admin route bypasses it."""
        other = make_user(role=UserRole.teacher, username="other_t_lab")
        db_session.add(other)
        await db_session.flush()
        fcls = Class(name="Foreign", teacher_id=other.id, invite_code="FRGNLB")
        db_session.add(fcls)
        await db_session.flush()
        flab = Lab(class_id=fcls.id, name="Foreign Lab")
        db_session.add(flab)
        await db_session.commit()

        resp = await teacher_client.put(
            f"/api/teacher/labs/{flab.id}", json={"is_active": False})
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
        # analytics tokens are billed tokens (reconcile with quota), so the
        # llm message's billed_tokens=9, not the raw 25.
        assert body["total_tokens"] == 9
        assert len(body["labs"]) >= 1
        assert len(body["labs"][0]["students"]) >= 1

    async def test_request_count_is_per_exchange_not_per_message(self, teacher_client, seed_data):
        # seed_data has 1 user + 1 llm message = ONE exchange. Counting
        # all Message rows would report 2; an exchange is one llm reply.
        resp = await teacher_client.get(
            f"/api/teacher/analytics/classes/{seed_data['class'].id}")
        body = resp.json()
        assert body["total_requests"] == 1
        assert body["labs"][0]["students"][0]["request_count"] == 1

    async def test_wrong_class_404(self, teacher_client):
        resp = await teacher_client.get(
            f"/api/teacher/analytics/classes/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_client, seed_data):
        resp = await student_client.get(
            f"/api/teacher/analytics/classes/{seed_data['class'].id}")
        assert resp.status_code == 403


# ===================================================================
# Skill presets (a teacher's own rules)
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

    async def test_student_cannot_create_preset_403(self, student_client):
        resp = await student_client.post("/api/teacher/skill-presets",
                                         json={"name": "x", "content": "y"})
        assert resp.status_code == 403

    async def test_chat_history_scoped_to_own_classes(self, teacher_client, seed_data, db_session):
        # A foreign teacher's class/lab/session must be invisible even
        # when its session_id is supplied directly (IDOR).
        other = make_user(role=UserRole.teacher, username="other_t2")
        stu = make_user(role=UserRole.student, username="foreign_stu")
        db_session.add_all([other, stu])
        await db_session.flush()
        fcls = Class(name="Foreign", teacher_id=other.id, invite_code="FRGN01")
        db_session.add(fcls)
        await db_session.flush()
        flab = Lab(class_id=fcls.id, name="Foreign Lab")
        db_session.add(flab)
        await db_session.flush()
        fsess = Session(user_id=stu.id, lab_id=flab.id, title="secret")
        db_session.add(fsess)
        await db_session.flush()
        db_session.add(Message(session_id=fsess.id, sender=SenderType.user, content="secret"))
        await db_session.commit()

        resp = await teacher_client.get(f"/api/teacher/chat-history?session_id={fsess.id}")
        assert resp.status_code == 200
        assert resp.json() == []  # leak blocked — not in teacher1's owned labs

    async def test_chat_history_still_shows_own_sessions(self, teacher_client, seed_data):
        # teacher1 owns seed_data["class"]/lab → their student's session is visible.
        sess_id = seed_data["session"].id
        resp = await teacher_client.get(f"/api/teacher/chat-history?session_id={sess_id}")
        assert resp.status_code == 200
        assert [s["id"] for s in resp.json()] == [str(sess_id)]

