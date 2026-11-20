"""test_search.py — searching conversations (a student's own, a teacher's lab)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (AgentRun, Class, ClassStudent, Lab, Message, MessageCitation, SenderType,
                                Session, UserRole)
from backend.app.services.search_service import snippet
from tests.conftest import make_client, make_user

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def msg(session, sender, content, minutes):
    return Message(session_id=session.id, sender=sender, content=content, created_at=T0 + timedelta(minutes=minutes))


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> dict:
    teacher = make_user(role=UserRole.teacher, username='teacher')
    other_teacher = make_user(role=UserRole.teacher, username='other')
    lea, hugo = make_user(username='lea'), make_user(username='hugo')
    db_session.add_all([teacher, other_teacher, lea, hugo])
    await db_session.flush()
    cls = Class(name='Python S5', teacher_id=teacher.id, invite_code='SRCH01')
    db_session.add(cls)
    await db_session.flush()
    lab, lab2 = Lab(class_id=cls.id, name='TP3'), Lab(class_id=cls.id, name='TP2')
    db_session.add_all([lab, lab2])
    await db_session.flush()
    db_session.add_all([ClassStudent(class_id=cls.id, student_id=lea.id),
                        ClassStudent(class_id=cls.id, student_id=hugo.id)])
    s1 = Session(user_id=lea.id, lab_id=lab.id, title='Factorielle', created_at=T0)
    s2 = Session(user_id=lea.id, lab_id=lab.id, title='Récursivité terminale', created_at=T0)
    gone = Session(user_id=lea.id, lab_id=lab.id, title='Hidden', is_deleted=True, created_at=T0)
    test = Session(user_id=teacher.id, lab_id=lab.id, title='Test drive', is_test=True, created_at=T0)
    s3 = Session(user_id=hugo.id, lab_id=lab.id, title='Boucle infinie', created_at=T0)
    s4 = Session(user_id=hugo.id, lab_id=lab2.id, title='TP2', created_at=T0)
    db_session.add_all([s1, s2, gone, test, s3, s4])
    await db_session.flush()
    q3, a3 = msg(s3, SenderType.user, "Ma récursion ne s'arrête jamais", 30), msg(s3, SenderType.llm, 'Il manque un cas de base.', 31)
    db_session.add_all([
        msg(s1, SenderType.user, 'Pourquoi factorielle(0) vaut 1 ?', 1),
        msg(s1, SenderType.llm, 'Le cas de base arrête la Récursion : 0! = 1.', 2),
        msg(s2, SenderType.user, 'Python optimise-t-il ?', 3),
        msg(gone, SenderType.user, 'récursion secrète', 10),
        msg(test, SenderType.user, 'récursion du prof', 20),
        q3, a3,
        msg(s4, SenderType.user, 'récursion dans le TP2', 40),
    ])
    await db_session.flush()
    db_session.add(AgentRun(user_id=hugo.id, session_id=s3.id, client_request_id='r1', question=q3.content,
                            status='finished', user_message_id=q3.id, assistant_message_id=a3.id, mode='research'))
    db_session.add(MessageCitation(message_id=a3.id, label='E1', document_id=uuid.uuid4(), revision_id=uuid.uuid4(),
                                   page_id=uuid.uuid4(), pdf_page_no=7, origin='research'))
    await db_session.commit()
    return {'teacher': teacher, 'other': other_teacher, 'lea': lea, 'hugo': hugo, 'lab': lab,
            's1': s1, 's2': s2, 'gone': gone, 's3': s3}


def test_snippet_keeps_the_match_in_view():
    text = 'a ' * 200 + 'le cas de base' + ' b' * 200
    out = snippet(text, 'CAS DE BASE')
    assert 'cas de base' in out and out.startswith('…') and out.endswith('…') and len(out) <= 142


@pytest.mark.asyncio
async def test_a_student_finds_only_their_visible_chats(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        hits = (await c.get('/api/student/search', params={'q': 'récursion'})).json()
        assert [h['session_id'] for h in hits] == [str(seed['s1'].id)]          # not deleted, not hugo's
        assert hits[0]['role'] == 'assistant' and 'Récursion' in hits[0]['snippet']
        assert hits[0]['lab_name'] == 'TP3' and hits[0]['class_name'] == 'Python S5'
        titled = (await c.get('/api/student/search', params={'q': 'terminale'})).json()
        assert [(h['title'], h['message_id']) for h in titled] == [('Récursivité terminale', None)]
        assert (await c.get('/api/student/search', params={'q': '100%'})).json() == []
        assert (await c.get('/api/student/search', params={'q': 'x'})).status_code == 422


@pytest.mark.asyncio
async def test_a_teacher_searches_the_questions_of_a_lab(db_session, seed):
    async with await make_client(db_session, seed['teacher']) as c:
        out = (await c.get(f"/api/teacher/labs/{seed['lab'].id}/search", params={'q': 'récursion'})).json()
    # Questions only (not answers), deleted sessions included, test drive and other labs left out.
    assert out['total'] == 2 and out['students'] == 2
    first, second = out['items']
    assert (first['student_name'], first['cited'], first['answer_mode']) == ('hugo', True, 'research')
    assert (second['student_name'], second['session_deleted'], second['cited']) == ('lea', True, False)


@pytest.mark.asyncio
async def test_another_teacher_cannot_search_the_lab(db_session, seed):
    async with await make_client(db_session, seed['other']) as c:
        out = await c.get(f"/api/teacher/labs/{seed['lab'].id}/search", params={'q': 'récursion'})
    assert out.status_code == 403
    async with await make_client(db_session, seed['lea']) as c:
        out = await c.get(f"/api/teacher/labs/{seed['lab'].id}/search", params={'q': 'récursion'})
    assert out.status_code == 403


@pytest.mark.asyncio
async def test_postgres_ignores_the_case_of_accented_letters(pg_session):
    from backend.app.services.search_service import student_search
    lea = make_user(username=f'lea-{uuid.uuid4().hex[:6]}')
    pg_session.add(lea)
    await pg_session.flush()
    chat = Session(user_id=lea.id, lab_id=None, title='Cas de base')
    pg_session.add(chat)
    await pg_session.flush()
    pg_session.add(msg(chat, SenderType.llm, 'Le cas de base arrête la RÉCURSION.', 1))
    await pg_session.commit()
    hits = await student_search(pg_session, lea.id, 'récursion')
    assert [h['session_id'] for h in hits] == [chat.id]
