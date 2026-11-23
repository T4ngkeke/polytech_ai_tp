"""test_settings_export.py — user settings, pinned chats, exporting one's chats."""
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Class, ClassStudent, Lab, Message, SenderType, Session, UserRole
from tests.conftest import make_client, make_user


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> dict:
    teacher = make_user(role=UserRole.teacher, username='teacher')
    lea = make_user(username='lea')
    hugo = make_user(username='hugo')
    db_session.add_all([teacher, lea, hugo])
    await db_session.flush()
    cls = Class(name='Python S5', teacher_id=teacher.id, invite_code='SETT01')
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(class_id=cls.id, name='TP3')
    db_session.add(lab)
    await db_session.flush()
    db_session.add(ClassStudent(class_id=cls.id, student_id=lea.id))
    kept = Session(user_id=lea.id, lab_id=lab.id, title='Factorielle')
    gone = Session(user_id=lea.id, lab_id=lab.id, title='Hidden', is_deleted=True)
    other = Session(user_id=hugo.id, lab_id=lab.id, title='Hugo chat')
    db_session.add_all([kept, gone, other])
    await db_session.flush()
    db_session.add_all([
        Message(session_id=kept.id, sender=SenderType.user, content="C'est quoi le cas de base ?"),
        Message(session_id=kept.id, sender=SenderType.llm, content='Le cas de base arrête la récursion [E1].'),
        Message(session_id=gone.id, sender=SenderType.user, content='secret'),
        Message(session_id=other.id, sender=SenderType.user, content='not mine'),
    ])
    await db_session.commit()
    return {'lea': lea, 'hugo': hugo, 'kept': kept, 'other': other}


@pytest.mark.asyncio
async def test_settings_change_only_what_is_sent(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        out = (await c.put('/api/auth/settings', json={'ui_language': 'fr'})).json()
        assert out['ui_language'] == 'fr' and out['about_me'] is None
        out = (await c.put('/api/auth/settings', json={'about_me': '  Je débute en Python.  '})).json()
        assert out['ui_language'] == 'fr' and out['about_me'] == 'Je débute en Python.'
        me = (await c.get('/api/auth/me')).json()
        assert me['ui_language'] == 'fr' and me['about_me'] == 'Je débute en Python.'
        out = (await c.put('/api/auth/settings', json={'ui_language': None, 'about_me': ''})).json()
        assert out['ui_language'] is None and out['about_me'] is None
        for bad in ({'ui_language': 'de'}, {'about_me': 'x' * 501}, {'theme': 'dark'}):
            assert (await c.put('/api/auth/settings', json=bad)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('role', [UserRole.teacher, UserRole.admin])
async def test_teachers_and_admins_choose_their_language_too(db_session, role):
    user = make_user(role=role, username=f'lang-{role.value}')
    db_session.add(user)
    await db_session.commit()
    async with await make_client(db_session, user) as c:
        assert (await c.put('/api/auth/settings', json={'ui_language': 'zh'})).json()['ui_language'] == 'zh'
        assert (await c.get('/api/auth/me')).json()['ui_language'] == 'zh'


@pytest.mark.asyncio
async def test_pin_and_rename_a_chat(db_session, seed):
    sid = seed['kept'].id
    async with await make_client(db_session, seed['lea']) as c:
        out = (await c.put(f'/api/student/sessions/{sid}', json={'pinned': True})).json()
        assert out['pinned'] is True and out['title'] == 'Factorielle'
        out = (await c.put(f'/api/student/sessions/{sid}', json={'title': 'Base case'})).json()
        assert out['pinned'] is True and out['title'] == 'Base case'
        listed = (await c.get('/api/student/sessions')).json()
        assert [(s['title'], s['pinned']) for s in listed] == [('Base case', True)]
        assert (await c.put(f'/api/student/sessions/{sid}', json={})).status_code == 422
        other = await c.put(f"/api/student/sessions/{seed['other'].id}", json={'pinned': True})
        assert other.status_code == 403


@pytest.mark.asyncio
async def test_export_has_only_my_visible_chats(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        out = await c.get('/api/student/export')
        assert out.status_code == 200
        assert out.headers['content-disposition'].startswith('attachment; filename="edu-llm-')
        data = json.loads(out.text)
        assert data['user'] == 'lea'
        (conv,) = data['conversations']
        assert conv['title'] == 'Factorielle' and conv['class'] == 'Python S5' and conv['lab'] == 'TP3'
        assert [m['role'] for m in conv['messages']] == ['student', 'assistant']
        md = await c.get('/api/student/export?format=md')
        assert md.headers['content-type'].startswith('text/markdown')
        assert '## Factorielle' in md.text and 'arrête la récursion [E1]' in md.text
        assert 'secret' not in md.text and 'not mine' not in md.text
        assert (await c.get('/api/student/export?format=pdf')).status_code == 422
