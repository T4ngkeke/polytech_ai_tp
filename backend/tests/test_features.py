"""test_features.py — what students may use in a class and a lab.

Covers services/feature_service.py (class switches, lab choices, exam mode),
the teacher routes PUT /classes/{id}/features and PUT /labs/{id}/features,
the final features in the teacher and student lab lists, and the chat
refusing a question whose tool is off.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import ChatRefusal, Class, ClassStudent, Lab, Session, UserRole
from backend.app.services import feature_service as fs
from tests.conftest import make_client, make_user

ALL_ON = {'upload': True, 'web': True, 'code': True, 'deep': True}
ALL_OFF = {'upload': False, 'web': False, 'code': False, 'deep': False}


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def test_a_missing_class_switch_is_on():
    assert fs.class_features(None) == ALL_ON
    assert fs.class_features({'web': False}) == {**ALL_ON, 'web': False}


def test_a_missing_or_unknown_lab_choice_follows_the_class():
    assert fs.lab_overrides({'web': 'off', 'code': 'maybe'}) == {
        'upload': 'inherit', 'web': 'off', 'code': 'inherit', 'deep': 'inherit'}


def test_the_lab_choice_wins_over_the_class_switch():
    out = fs.effective({'web': False, 'code': True}, {'web': 'on', 'code': 'off'}, exam_mode=False)
    assert out == {'upload': True, 'web': True, 'code': False, 'deep': True}


def test_exam_mode_turns_everything_off():
    assert fs.effective({}, {'web': 'on'}, exam_mode=True) == ALL_OFF


def test_updates_change_only_the_given_features():
    assert fs.updated_class({'web': False}, {'code': False}) == {**ALL_ON, 'web': False, 'code': False}
    # 'inherit' removes the lab's own choice; the others stay.
    assert fs.updated_lab({'web': 'off', 'code': 'on'}, {'web': 'inherit', 'deep': 'off'}) == {
        'code': 'on', 'deep': 'off'}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> dict:
    teacher = make_user(role=UserRole.teacher, username='teacher')
    other = make_user(role=UserRole.teacher, username='other')
    student = make_user(role=UserRole.student, username='student')
    db_session.add_all([teacher, other, student])
    await db_session.flush()
    cls = Class(name='Python', teacher_id=teacher.id, invite_code='FEAT01')
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(class_id=cls.id, name='TP3')
    db_session.add(lab)
    await db_session.flush()
    db_session.add(ClassStudent(class_id=cls.id, student_id=student.id))
    sess = Session(user_id=student.id, lab_id=lab.id, title='Q')
    db_session.add(sess)
    await db_session.commit()
    return {'teacher': teacher, 'other': other, 'student': student, 'class': cls, 'lab': lab, 'sess': sess}


@pytest.mark.asyncio
async def test_a_new_class_has_every_feature_on(db_session, seed):
    async with await make_client(db_session, seed['teacher']) as c:
        out = await c.post('/api/teacher/classes', json={'name': 'New'})
    assert out.status_code == 201 and out.json()['features'] == ALL_ON


@pytest.mark.asyncio
async def test_teacher_switches_some_class_features(db_session, seed):
    async with await make_client(db_session, seed['teacher']) as c:
        out = await c.put(f"/api/teacher/classes/{seed['class'].id}/features", json={'web': False})
        assert out.status_code == 200 and out.json()['features'] == {**ALL_ON, 'web': False}
        out = await c.put(f"/api/teacher/classes/{seed['class'].id}/features", json={'code': False})
        assert out.json()['features'] == {**ALL_ON, 'web': False, 'code': False}   # web kept
        bad = await c.put(f"/api/teacher/classes/{seed['class'].id}/features", json={'camera': False})
        assert bad.status_code == 422
        labs = (await c.get(f"/api/teacher/classes/{seed['class'].id}/labs")).json()
    assert labs[0]['features'] == {**ALL_ON, 'web': False, 'code': False}


@pytest.mark.asyncio
async def test_only_the_owner_switches_class_features(db_session, seed):
    async with await make_client(db_session, seed['other']) as c:
        out = await c.put(f"/api/teacher/classes/{seed['class'].id}/features", json={'web': False})
    assert out.status_code == 403
    async with await make_client(db_session, seed['student']) as c:
        out = await c.put(f"/api/teacher/classes/{seed['class'].id}/features", json={'web': False})
    assert out.status_code == 403


@pytest.mark.asyncio
async def test_teacher_sets_and_clears_a_lab_choice(db_session, seed):
    async with await make_client(db_session, seed['teacher']) as c:
        await c.put(f"/api/teacher/classes/{seed['class'].id}/features", json={'web': False})
        out = await c.put(f"/api/teacher/labs/{seed['lab'].id}/features", json={'web': 'on', 'deep': 'off'})
        body = out.json()
        assert out.status_code == 200
        assert body['feature_overrides'] == {'upload': 'inherit', 'web': 'on', 'code': 'inherit', 'deep': 'off'}
        assert body['features'] == {**ALL_ON, 'deep': False}            # web on here, off in the class
        out = await c.put(f"/api/teacher/labs/{seed['lab'].id}/features", json={'web': 'inherit'})
        assert out.json()['features'] == {**ALL_ON, 'web': False, 'deep': False}
        bad = await c.put(f"/api/teacher/labs/{seed['lab'].id}/features", json={'web': True})
        assert bad.status_code == 422
    async with await make_client(db_session, seed['other']) as c:
        out = await c.put(f"/api/teacher/labs/{seed['lab'].id}/features", json={'web': 'on'})
    assert out.status_code == 403


@pytest.mark.asyncio
async def test_students_see_what_they_may_use(db_session, seed):
    seed['class'].features = {'code': False}
    await db_session.commit()
    async with await make_client(db_session, seed['student']) as c:
        labs = (await c.get(f"/api/student/classes/{seed['class'].id}/labs")).json()
        assert labs[0]['features'] == {**ALL_ON, 'code': False}
        seed['class'].exam_mode = True
        await db_session.commit()
        labs = (await c.get(f"/api/student/classes/{seed['class'].id}/labs")).json()
    assert labs[0]['features'] == ALL_OFF


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

async def _ask(db_session, seed, tools):
    async with await make_client(db_session, seed['student']) as c:
        return await c.post('/api/chat/stream', json={
            'session_id': str(seed['sess'].id), 'message': 'hi', 'tools': tools})


@pytest.mark.asyncio
async def test_chat_refuses_a_tool_that_is_off(db_session, seed):
    seed['class'].features = {'web': False}
    await db_session.commit()
    out = await _ask(db_session, seed, ['web'])
    assert out.status_code == 403 and out.json()['detail'] == 'feature_off'
    reasons = (await db_session.execute(select(ChatRefusal.reason))).scalars().all()
    assert reasons == ['feature_off']


@pytest.mark.asyncio
async def test_chat_refuses_every_tool_in_exam_mode(db_session, seed):
    seed['class'].exam_mode = True
    await db_session.commit()
    out = await _ask(db_session, seed, ['deep'])
    assert out.status_code == 403 and out.json()['detail'] == 'feature_off'


@pytest.mark.asyncio
async def test_chat_lets_an_allowed_tool_through(db_session, seed):
    seed['lab'].feature_overrides = {'web': 'on'}
    seed['class'].features = {'web': False}
    await db_session.commit()
    out = await _ask(db_session, seed, ['web'])
    # Past the gate: whatever happens next, it is not the feature refusal.
    assert out.status_code != 403
    out = await _ask(db_session, seed, ['camera'])
    assert out.status_code == 422
