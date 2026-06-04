"""
test_admin.py — Tests for admin endpoints (v5 production-ready).
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    Class, ClassStudent, Lab, Message, SenderType,
    Session, SystemConfig, UsageStat, User, UserRole,
)
from tests.conftest import make_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed_users(db_session: AsyncSession) -> dict[str, User]:
    admin = make_user(role=UserRole.admin, username="admin1")
    teacher = make_user(role=UserRole.teacher, username="teacher1")
    student_a = make_user(role=UserRole.student, username="student_a")
    student_b = make_user(role=UserRole.student, username="student_b")
    deleted = make_user(role=UserRole.student, username="deleted_s", is_deleted=True)
    db_session.add_all([admin, teacher, student_a, student_b, deleted])
    await db_session.commit()
    for u in [admin, teacher, student_a, student_b, deleted]:
        await db_session.refresh(u)
    return {"admin": admin, "teacher": teacher, "student_a": student_a,
            "student_b": student_b, "deleted": deleted}


@pytest_asyncio.fixture
async def seed_full(db_session: AsyncSession) -> dict:
    admin = make_user(role=UserRole.admin, username="admin1")
    teacher = make_user(role=UserRole.teacher, username="teacher1")
    teacher2 = make_user(role=UserRole.teacher, username="teacher2")
    student_a = make_user(role=UserRole.student, username="student_a")
    student_b = make_user(role=UserRole.student, username="student_b")
    db_session.add_all([admin, teacher, teacher2, student_a, student_b])
    await db_session.flush()

    cls = Class(name="Admin Test Class", teacher_id=teacher.id, invite_code="ADM001")
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(class_id=cls.id, name="Admin Test Lab")
    db_session.add(lab)
    await db_session.flush()
    deleted_cls = Class(name="Deleted Class", teacher_id=teacher.id,
                        invite_code="DEL001", is_deleted=True)
    db_session.add(deleted_cls)
    await db_session.flush()
    deleted_lab = Lab(class_id=cls.id, name="Deleted Lab", is_deleted=True)
    db_session.add(deleted_lab)
    await db_session.flush()

    db_session.add_all([
        ClassStudent(class_id=cls.id, student_id=student_a.id),
        ClassStudent(class_id=cls.id, student_id=student_b.id),
    ])
    await db_session.flush()

    sess = Session(user_id=student_a.id, lab_id=lab.id, title="Test Session")
    db_session.add(sess)
    await db_session.flush()
    m1 = Message(session_id=sess.id, sender=SenderType.user, content="Hello", total_tokens=10)
    m2 = Message(session_id=sess.id, sender=SenderType.llm, content="Hi!", total_tokens=15)
    db_session.add_all([m1, m2])

    old_sess = Session(user_id=student_a.id, lab_id=lab.id, title="Old Session")
    db_session.add(old_sess)
    await db_session.flush()
    old_sess.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    db_session.add(old_sess)
    old_msg = Message(session_id=old_sess.id, sender=SenderType.user,
                      content="Old message", total_tokens=5)
    db_session.add(old_msg)

    usage = UsageStat(user_id=student_a.id, date=date.today(),
                      tokens_used=1234, request_count=5)
    db_session.add(usage)
    await db_session.commit()
    for obj in [admin, teacher, teacher2, student_a, student_b, cls, lab,
                sess, old_sess, deleted_cls, deleted_lab]:
        await db_session.refresh(obj)
    return {
        "admin": admin, "teacher": teacher, "teacher2": teacher2,
        "student_a": student_a, "student_b": student_b,
        "class": cls, "lab": lab, "session": sess,
        "old_session": old_sess, "deleted_class": deleted_cls,
        "deleted_lab": deleted_lab,
    }


@pytest_asyncio.fixture
async def admin_client(db_session, seed_users):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(user_id=seed_users["admin"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_full_client(db_session, seed_full):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(user_id=seed_full["admin"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def student_client(db_session, seed_users):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(user_id=seed_users["student_a"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def student_full_client(db_session, seed_full):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(user_id=seed_full["student_a"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


# ===================================================================
# Original endpoints
# ===================================================================

class TestListUsers:
    async def test_admin_gets_non_deleted_users(self, admin_client, seed_users):
        resp = await admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()]
        assert "deleted_s" not in usernames
        assert len(resp.json()) == 4

    async def test_student_rejected_403(self, student_client):
        resp = await student_client.get("/api/admin/users")
        assert resp.status_code == 403

    async def test_unauthenticated_401(self, db_session):
        async def override():
            yield db_session
        app.dependency_overrides[get_db] = override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/admin/users")
        app.dependency_overrides.clear()
        assert resp.status_code == 401


class TestCreateUser:
    async def test_admin_creates_user(self, admin_client):
        resp = await admin_client.post("/api/admin/users", json={
            "username": "new_user", "password": "securePass1",
            "role": "student", "daily_token_quota": 30_000,
        })
        assert resp.status_code == 201
        assert resp.json()["username"] == "new_user"

    async def test_duplicate_username_409(self, admin_client, seed_users):
        resp = await admin_client.post("/api/admin/users", json={
            "username": "student_a", "password": "securePass1",
        })
        assert resp.status_code == 409

    async def test_student_cannot_create_403(self, student_client):
        resp = await student_client.post("/api/admin/users", json={
            "username": "hacker", "password": "securePass1",
        })
        assert resp.status_code == 403


class TestUpdateQuota:
    async def test_admin_updates_quota(self, admin_client, seed_users):
        resp = await admin_client.put(
            f"/api/admin/users/{seed_users['student_a'].id}/quota",
            json={"daily_token_quota": 10_000},
        )
        assert resp.status_code == 200
        assert resp.json()["daily_token_quota"] == 10_000

    async def test_nonexistent_user_404(self, admin_client):
        resp = await admin_client.put(
            f"/api/admin/users/{uuid.uuid4()}/quota",
            json={"daily_token_quota": 100},
        )
        assert resp.status_code == 404

    async def test_negative_quota_422(self, admin_client, seed_users):
        resp = await admin_client.put(
            f"/api/admin/users/{seed_users['student_a'].id}/quota",
            json={"daily_token_quota": -5},
        )
        assert resp.status_code == 422


class TestUpdateRole:
    async def test_admin_changes_role(self, admin_client, seed_users):
        resp = await admin_client.put(
            f"/api/admin/users/{seed_users['student_a'].id}/role",
            json={"role": "teacher"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "teacher"

    async def test_nonexistent_user_404(self, admin_client):
        resp = await admin_client.put(
            f"/api/admin/users/{uuid.uuid4()}/role", json={"role": "teacher"},
        )
        assert resp.status_code == 404

    async def test_student_cannot_change_role_403(self, student_client, seed_users):
        resp = await student_client.put(
            f"/api/admin/users/{seed_users['student_b'].id}/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 403


class TestDeleteUser:
    async def test_admin_soft_deletes(self, admin_client, seed_users, db_session):
        student = seed_users["student_a"]
        resp = await admin_client.delete(f"/api/admin/users/{student.id}")
        assert resp.status_code == 204
        await db_session.refresh(student)
        assert student.is_deleted is True

    async def test_already_deleted_404(self, admin_client, seed_users):
        resp = await admin_client.delete(f"/api/admin/users/{seed_users['deleted'].id}")
        assert resp.status_code == 404

    async def test_student_cannot_delete_403(self, student_client, seed_users):
        resp = await student_client.delete(f"/api/admin/users/{seed_users['student_b'].id}")
        assert resp.status_code == 403


class TestLLMConfig:
    async def test_admin_upserts_llm_config(self, admin_client):
        resp = await admin_client.put("/api/admin/llm/config", json={
            "base_url": "http://newhost:11434/v1", "api_key": "new-key", "model": "llama3",
        })
        assert resp.status_code == 200
        assert resp.json()["model"] == "llama3"

    async def test_admin_reads_llm_config(self, admin_client):
        await admin_client.put("/api/admin/llm/config", json={
            "base_url": "http://test:1234/v1", "api_key": "key123", "model": "qwen",
        })
        resp = await admin_client.get("/api/admin/llm/config")
        assert resp.status_code == 200
        assert resp.json()["model"] == "qwen"

    async def test_upsert_overwrites(self, admin_client):
        await admin_client.put("/api/admin/llm/config", json={
            "base_url": "http://first/v1", "api_key": "k1", "model": "m1",
        })
        await admin_client.put("/api/admin/llm/config", json={
            "base_url": "http://second/v1", "api_key": "k2", "model": "m2",
        })
        resp = await admin_client.get("/api/admin/llm/config")
        assert resp.json()["model"] == "m2"

    async def test_student_cannot_upsert_403(self, student_client):
        resp = await student_client.put("/api/admin/llm/config", json={
            "base_url": "http://x", "api_key": "x", "model": "x",
        })
        assert resp.status_code == 403


class TestCSVImport:
    def _csv(self, rows):
        return "\n".join(rows).encode("utf-8")

    async def test_dry_run_preview(self, admin_client, seed_users):
        csv = self._csv(["username,password,role", "brand_new,pass1234,student",
                         "student_a,newpass,student"])
        resp = await admin_client.post("/api/admin/users/import?force=false",
                                       files={"file": ("u.csv", csv, "text/csv")})
        assert resp.status_code == 200
        assert resp.json()["to_create"] == 1
        assert resp.json()["forced"] is False

    async def test_force_import(self, admin_client, seed_users):
        csv = self._csv(["username,password,role", "csv_new,p123,student",
                         "student_a,upd,student"])
        resp = await admin_client.post("/api/admin/users/import?force=true",
                                       files={"file": ("u.csv", csv, "text/csv")})
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

    async def test_empty_csv_400(self, admin_client):
        resp = await admin_client.post("/api/admin/users/import",
                                       files={"file": ("e.csv", b"username,password,role\n", "text/csv")})
        assert resp.status_code == 400

    async def test_missing_columns_400(self, admin_client):
        resp = await admin_client.post("/api/admin/users/import",
                                       files={"file": ("b.csv", b"name,pass\nfoo,bar\n", "text/csv")})
        assert resp.status_code == 400

    async def test_student_cannot_import_403(self, student_client):
        resp = await student_client.post("/api/admin/users/import",
                                          files={"file": ("u.csv", b"username,password\nfoo,bar\n", "text/csv")})
        assert resp.status_code == 403


# ===================================================================
# New endpoints
# ===================================================================

class TestAdminListClasses:
    async def test_lists_non_deleted(self, admin_full_client, seed_full):
        resp = await admin_full_client.get("/api/admin/classes")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Admin Test Class" in names
        assert "Deleted Class" not in names

    async def test_pagination(self, admin_full_client, seed_full):
        resp = await admin_full_client.get("/api/admin/classes?skip=0&limit=1")
        assert resp.status_code == 200
        assert len(resp.json()) <= 1

    async def test_student_rejected_403(self, student_full_client):
        resp = await student_full_client.get("/api/admin/classes")
        assert resp.status_code == 403


class TestAdminListClassStudents:
    async def test_lists_enrolled(self, admin_full_client, seed_full):
        resp = await admin_full_client.get(
            f"/api/admin/classes/{seed_full['class'].id}/students")
        assert resp.status_code == 200
        usernames = [s["username"] for s in resp.json()]
        assert "student_a" in usernames
        assert "student_b" in usernames

    async def test_nonexistent_class_404(self, admin_full_client):
        resp = await admin_full_client.get(
            f"/api/admin/classes/{uuid.uuid4()}/students")
        assert resp.status_code == 404


class TestAdminListLabs:
    async def test_lists_non_deleted(self, admin_full_client, seed_full):
        resp = await admin_full_client.get("/api/admin/labs")
        assert resp.status_code == 200
        names = [l["name"] for l in resp.json()]
        assert "Admin Test Lab" in names
        assert "Deleted Lab" not in names


class TestAdminTransferClass:
    async def test_successful_transfer(self, admin_full_client, seed_full):
        resp = await admin_full_client.put(
            f"/api/admin/classes/{seed_full['class'].id}/transfer",
            json={"teacher_id": str(seed_full["teacher2"].id)},
        )
        assert resp.status_code == 200
        assert resp.json()["teacher_id"] == str(seed_full["teacher2"].id)

    async def test_nonexistent_teacher_404(self, admin_full_client, seed_full):
        resp = await admin_full_client.put(
            f"/api/admin/classes/{seed_full['class'].id}/transfer",
            json={"teacher_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404

    async def test_transfer_to_student_404(self, admin_full_client, seed_full):
        resp = await admin_full_client.put(
            f"/api/admin/classes/{seed_full['class'].id}/transfer",
            json={"teacher_id": str(seed_full["student_a"].id)},
        )
        assert resp.status_code == 404

    async def test_nonexistent_class_404(self, admin_full_client):
        resp = await admin_full_client.put(
            f"/api/admin/classes/{uuid.uuid4()}/transfer",
            json={"teacher_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404


class TestAdminAnalytics:
    async def test_returns_stats(self, admin_full_client, seed_full):
        resp = await admin_full_client.get("/api/admin/analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tokens"] >= 1234
        assert body["total_requests"] >= 5
        assert len(body["daily_breakdown"]) >= 1

    async def test_empty_state(self, admin_client):
        resp = await admin_client.get("/api/admin/analytics")
        assert resp.status_code == 200
        assert resp.json()["total_tokens"] == 0


class TestAdminHardDeleteSession:
    async def test_hard_deletes(self, admin_full_client, seed_full, db_session):
        sid = seed_full["session"].id
        resp = await admin_full_client.delete(f"/api/admin/sessions/{sid}")
        assert resp.status_code == 204
        r = await db_session.execute(select(Session).where(Session.id == sid))
        assert r.scalar_one_or_none() is None
        mr = await db_session.execute(select(Message).where(Message.session_id == sid))
        assert len(mr.scalars().all()) == 0

    async def test_nonexistent_404(self, admin_full_client):
        resp = await admin_full_client.delete(f"/api/admin/sessions/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_student_rejected_403(self, student_full_client, seed_full):
        resp = await student_full_client.delete(
            f"/api/admin/sessions/{seed_full['session'].id}")
        assert resp.status_code == 403


class TestAdminPrune:
    async def test_prunes_old(self, admin_full_client, seed_full, db_session):
        old_id = seed_full["old_session"].id
        resp = await admin_full_client.post("/api/admin/maintenance/prune",
                                            json={"older_than_days": 30})
        assert resp.status_code == 200
        assert resp.json()["sessions_deleted"] >= 1
        r = await db_session.execute(select(Session).where(Session.id == old_id))
        assert r.scalar_one_or_none() is None

    async def test_preserves_recent(self, admin_full_client, seed_full, db_session):
        recent_id = seed_full["session"].id
        await admin_full_client.post("/api/admin/maintenance/prune",
                                     json={"older_than_days": 30})
        r = await db_session.execute(select(Session).where(Session.id == recent_id))
        assert r.scalar_one_or_none() is not None

    async def test_nothing_to_prune(self, admin_full_client):
        resp = await admin_full_client.post("/api/admin/maintenance/prune",
                                            json={"older_than_days": 9999})
        assert resp.status_code == 200
        assert resp.json()["sessions_deleted"] == 0

    async def test_invalid_days_422(self, admin_full_client):
        resp = await admin_full_client.post("/api/admin/maintenance/prune",
                                            json={"older_than_days": 0})
        assert resp.status_code == 422

    async def test_student_rejected_403(self, student_full_client):
        resp = await student_full_client.post("/api/admin/maintenance/prune",
                                              json={"older_than_days": 30})
        assert resp.status_code == 403
