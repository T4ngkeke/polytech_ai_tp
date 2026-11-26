"""test_notifications.py — the notification bell.

Covers the routes (list, unread count, mark read), and each event that writes
notifications: exam mode, a lab locked or opened, quota requests and answers,
a course file published, a file failed or ready for its teacher.
"""
import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.models import Class, ClassStudent, Lab, Notification, UsageStat, UserRole
from backend.app.services import document_versions as versions
from backend.app.services import notification_service as ns
from backend.app.services import quota_service
from backend.tests.conftest import make_client, make_user
from backend.tests.test_document_versions import content, draft, published, seed as doc_seed


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> dict:
    teacher = make_user(role=UserRole.teacher, username='teacher')
    student = make_user(role=UserRole.student, username='lea', quota=100)
    other = make_user(role=UserRole.student, username='hugo', quota=100)
    outsider = make_user(role=UserRole.student, username='outsider')
    db_session.add_all([teacher, student, other, outsider])
    await db_session.flush()
    cls = Class(name='Python', teacher_id=teacher.id, invite_code='NOTI01')
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(class_id=cls.id, name='TP3')
    db_session.add(lab)
    db_session.add_all([ClassStudent(class_id=cls.id, student_id=student.id),
                        ClassStudent(class_id=cls.id, student_id=other.id)])
    await db_session.commit()
    return {'teacher': teacher, 'student': student, 'other': other, 'outsider': outsider,
            'class': cls, 'lab': lab}


async def kinds(db, user):
    rows = (await db.execute(select(Notification.kind, Notification.payload)
                             .where(Notification.user_id == user.id))).all()
    return [(kind, payload) for kind, payload in rows]


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        ns.notify(None, [uuid.uuid4()], 'birthday')


@pytest.mark.asyncio
async def test_list_and_mark_read(db_session, seed):
    ns.notify(db_session, [seed['student'].id], 'quota_reset')
    ns.notify(db_session, [seed['student'].id], 'quota_ignored')
    ns.notify(db_session, [seed['other'].id], 'quota_reset')
    await db_session.commit()
    async with await make_client(db_session, seed['student']) as c:
        out = (await c.get('/api/notifications')).json()
        assert out['unread'] == 2 and len(out['items']) == 2
        first = out['items'][0]['id']
        # Another user's id is not marked.
        other_id = (await db_session.scalar(select(Notification.id).where(
            Notification.user_id == seed['other'].id)))
        marked = (await c.post('/api/notifications/read', json={'ids': [first, str(other_id)]})).json()
        assert marked == {'marked': 1}
        assert (await c.get('/api/notifications')).json()['unread'] == 1
        assert (await c.post('/api/notifications/read', json={})).json() == {'marked': 1}
        out = (await c.get('/api/notifications')).json()
    assert out['unread'] == 0 and all(item['read'] for item in out['items'])
    async with await make_client(db_session, seed['other']) as c:
        assert (await c.get('/api/notifications')).json()['unread'] == 1


@pytest.mark.asyncio
async def test_exam_mode_tells_the_students_once(db_session, seed):
    async with await make_client(db_session, seed['teacher']) as c:
        url = f"/api/teacher/classes/{seed['class'].id}/exam-mode"
        await c.put(url, json={'exam_mode': True})
        await c.put(url, json={'exam_mode': True})            # no change: no second notification
    got = await kinds(db_session, seed['student'])
    assert got == [('exam_mode', {'class_id': str(seed['class'].id), 'class_name': 'Python', 'on': True})]
    assert await kinds(db_session, seed['other']) == got
    assert await kinds(db_session, seed['outsider']) == []
    assert await kinds(db_session, seed['teacher']) == []


@pytest.mark.asyncio
async def test_locking_and_opening_a_lab(db_session, seed):
    async with await make_client(db_session, seed['teacher']) as c:
        url = f"/api/teacher/labs/{seed['lab'].id}"
        await c.put(url, json={'name': 'TP3 bis'})             # a rename alone: nothing
        await c.put(url, json={'is_active': False})
        await c.put(url, json={'is_active': True})
    got = [(k, p['locked'], p['lab_name']) for k, p in await kinds(db_session, seed['student'])]
    assert sorted(got) == [('lab_locked', False, 'TP3 bis'), ('lab_locked', True, 'TP3 bis')]


async def _block(db, student):
    db.add(UsageStat(user_id=student.id, date=quota_service.today(), tokens_used=500, request_count=1))
    await db.commit()


@pytest.mark.asyncio
async def test_a_reset_request_and_its_answers(db_session, seed):
    student, teacher = seed['student'], seed['teacher']
    await _block(db_session, student)
    async with await make_client(db_session, student) as c:
        assert (await c.post('/api/student/usage/reset-request')).json()['sent'] is True
        await c.post('/api/student/usage/reset-request')      # still open: no second notification
    got = await kinds(db_session, teacher)
    assert got == [('quota_request', {'student_id': str(student.id), 'student_name': 'lea'})]

    async with await make_client(db_session, teacher) as c:
        await c.post(f'/api/teacher/quota-requests/{student.id}/ignore')
        assert [k for k, _ in await kinds(db_session, student)] == ['quota_ignored']
        await c.post(f'/api/teacher/quota-requests/{student.id}/unignore')
        assert await kinds(db_session, student) == []          # the undone ignore is withdrawn
        await c.post(f"/api/teacher/classes/{seed['class'].id}/students/{student.id}/quota-refill")
    assert [k for k, _ in await kinds(db_session, student)] == ['quota_reset']


@pytest.mark.asyncio
async def test_a_class_refill_does_not_tell_students_who_were_not_blocked(db_session, seed):
    await _block(db_session, seed['student'])
    async with await make_client(db_session, seed['teacher']) as c:
        await c.post(f"/api/teacher/classes/{seed['class'].id}/quota-refill-all")
    assert [k for k, _ in await kinds(db_session, seed['student'])] == ['quota_reset']
    assert await kinds(db_session, seed['other']) == []


# ---------------------------------------------------------------------------
# Course files
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
async def test_a_published_file_tells_the_students(db_session, tmp_path):
    doc, teacher, student, outsider, *_ = await published(db_session, tmp_path)
    got = await kinds(db_session, student)
    assert [(k, p['filename'], p['updated']) for k, p in got] == [('new_document', 'synthetic.odp', False)]
    assert await kinds(db_session, outsider) == []
    # A new version of the same file says so.
    rev = await draft(db_session, doc, teacher, tmp_path, 'two')
    await content(db_session, doc, rev, tmp_path)
    await versions.publish_revision(db_session, document_id=doc.id, revision_id=rev.id,
                                    teacher_id=teacher.id, expected_generation=doc.generation)
    await db_session.commit()
    assert sorted(p['updated'] for _, p in await kinds(db_session, student)) == [False, True]


@pytest.mark.asyncio
async def test_no_file_notice_during_an_exam_for_a_closed_file(db_session, tmp_path):
    doc, teacher, student, *_ = await doc_seed(db_session, tmp_path)
    (await db_session.get(Class, doc.class_id)).exam_mode = True
    await db_session.commit()
    rev = await draft(db_session, doc, teacher, tmp_path)
    await content(db_session, doc, rev, tmp_path)
    await versions.publish_revision(db_session, document_id=doc.id, revision_id=rev.id,
                                    teacher_id=teacher.id, expected_generation=doc.generation)
    await db_session.commit()
    assert await kinds(db_session, student) == []


@pytest.mark.asyncio
async def test_a_failed_file_tells_its_teacher(db_session, tmp_path):
    doc, teacher, student, *_ = await doc_seed(db_session, tmp_path)
    rev = await draft(db_session, doc, teacher, tmp_path)
    await versions.set_processing_result(db_session, document_id=doc.id, revision_id=rev.id,
                                         expected_generation=doc.generation, state='failed',
                                         report={'issues': ['unreadable']})
    await db_session.commit()
    assert [(k, p['filename']) for k, p in await kinds(db_session, teacher)] == [
        ('document_failed', 'synthetic.odp')]
    assert await kinds(db_session, student) == []


@pytest.mark.asyncio
async def test_a_read_file_tells_its_teacher_whether_pages_are_missing(db_session, tmp_path):
    doc, teacher, student, *_ = await doc_seed(db_session, tmp_path)
    await ns.notify_document_teacher(db_session, doc.id, 'document_ready', partial=True)
    await db_session.commit()
    (kind, payload), = await kinds(db_session, teacher)
    assert kind == 'document_ready' and payload['partial'] is True
    assert payload['lab_name'] == 'Lab' and payload['class_name'] == 'M1 synthetic'
