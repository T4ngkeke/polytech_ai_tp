"""test_memory_agent.py — recalling and reviewing a student's notes (ported from Open WebUI)."""
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.agent import memory as am
from backend.app.agent.course_tools import CourseTools
from backend.app.agent.model import ModelReply
from backend.app.agent.runner import ResearchRunner
from backend.app.models import Class, ClassStudent, Lab, StudentMemory, UserRole
from backend.app.services import memory_service
from backend.tests.conftest import make_user
from backend.tests.test_course_tools import ViewFixture
from backend.tests.test_runner import Script


def note(content, category=None, embedding=None):
    return {'content': content, 'category': category, 'embedding': embedding}


def test_all_notes_go_in_when_they_fit():
    block = am.memory_context([note('Préfère un indice avant la réponse.', 'préférences'),
                               note('Maîtrise le cas de base.')])
    assert block.startswith(am.MEMORY_CONTEXT_OPEN) and block.endswith(am.MEMORY_CONTEXT_CLOSE)
    assert '- préférences: Préfère un indice avant la réponse.' in block and '- Maîtrise le cas de base.' in block
    assert am.memory_context([]) == ''


def test_the_closest_notes_go_first_when_they_do_not_all_fit():
    near = note('N' * 150, embedding=[1.0, 0.0])
    far = [note(f'{i} ' + 'F' * 150, embedding=[0.0, 1.0]) for i in range(20)]
    block = am.memory_context(far + [near], query_vector=[1.0, 0.1])
    assert block.index('N' * 150) < block.index('F' * 150)
    assert len(block) < am.CONTEXT_CHAR_LIMIT + 300


def test_review_replies_are_checked():
    reply = 'Voici: {"operations": [' + ','.join([
        '{"action":"add","category":"préférences","content":"Préfère des exemples."}',
        '{"action":"replace","id":"m1","content":"Maîtrise la récursivité."}',
        '{"action":"replace","id":"other","content":"x"}',
        '{"action":"remove","id":"m2"}',
        '{"action":"move","id":"m1","path":"x"}',
        '{"action":"add","content":"' + 'x' * 201 + '"}']) + ']} fin'
    ops = am.valid_operations(am.parse_operations(reply), {'m1', 'm2'})
    assert [(o['action'], o.get('id')) for o in ops] == [('add', None), ('replace', 'm1'), ('remove', 'm2')]
    assert am.parse_operations('not json') == [] and am.parse_operations('{"operations": 3}') == []


def test_a_review_every_four_student_turns():
    assert [n for n in range(1, 13) if am.due(n)] == [4, 8, 12]


@pytest.mark.asyncio
async def test_the_question_carries_the_notes():
    model = Script('Bonjour.')
    await ResearchRunner(CourseTools(ViewFixture()), model).run(
        'Exercice 3 ?', memory_context=am.memory_context([note('Préfère un indice.')]))
    assert '<memory_context>' in model.seen[0][-1]['content'] and 'Exercice 3 ?' in model.seen[0][-1]['content']


@pytest.mark.asyncio
async def test_a_review_applies_checked_operations(db_session):
    teacher, lea = make_user(role=UserRole.teacher), make_user(username='lea')
    db_session.add_all([teacher, lea])
    await db_session.flush()
    cls = Class(name='Python', teacher_id=teacher.id, invite_code='MEMR01')
    db_session.add(cls)
    await db_session.flush()
    old = await memory_service.add(db_session, user_id=lea.id, class_id=cls.id, content='Hésite sur la récursivité.')
    gone = await memory_service.add(db_session, user_id=lea.id, class_id=cls.id, content='Aime les longues réponses.')
    await db_session.commit()
    ops = {'operations': [
        {'action': 'add', 'category': 'préférences', 'content': 'Préfère un indice avant la réponse.'},
        {'action': 'replace', 'id': str(old.id), 'category': 'récursivité', 'content': 'Maîtrise le cas de base.'},
        {'action': 'remove', 'id': str(gone.id)}]}
    seen = []

    async def model(messages, max_output, timeout, *, purpose=''):
        seen.append((messages, purpose))
        return ModelReply(json.dumps(ops), [], 'fixture', 100, 30)

    async def embed(texts):
        return [[0.5] * 1024 for _ in texts]
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    applied = await am.review(factory, user_id=lea.id, class_id=cls.id, session_id=uuid.uuid4(), model=model,
                              embed=embed, messages=[{'role': 'user', 'content': 'Donne-moi un indice'},
                                                     {'role': 'assistant', 'content': 'Pense au cas de base.'}])
    assert applied == 3 and seen[0][1] == 'memory_review'
    assert f'id={old.id}' in seen[0][0][1]['content'] and 'student: Donne-moi un indice' in seen[0][0][1]['content']
    db_session.expire_all()
    rows = {m.content: m for m in (await db_session.scalars(select(StudentMemory))).all()}
    assert set(rows) == {'Préfère un indice avant la réponse.', 'Maîtrise le cas de base.'}
    assert rows['Maîtrise le cas de base.'].category == 'récursivité'


@pytest.mark.asyncio
async def test_a_failed_review_changes_nothing(db_session):
    async def model(messages, max_output, timeout, *, purpose=''):
        raise TimeoutError
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await am.review(factory, user_id=uuid.uuid4(), class_id=uuid.uuid4(), session_id=uuid.uuid4(),
                           model=model, messages=[]) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_the_chat_sends_the_notes_only_when_memory_is_on(pg_session, tmp_path, monkeypatch, enabled):
    from backend.tests.conftest import make_client
    from backend.tests.test_unified_entry import _chat
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path,
                                           [ModelReply('Indice : pense au cas de base.', [], 'fixture', 50, 20)])
    lab = await pg_session.get(Lab, chat.lab_id)
    await memory_service.add(pg_session, user_id=student.id, class_id=lab.class_id,
                             content='Préfère un indice avant la réponse.', category='préférences')
    student.memory_enabled = enabled
    await pg_session.commit()
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Exercice 2 ?', 'client_request_id': str(uuid.uuid4())})
    assert response.status_code == 200
    question = seen[0][-1]['content']
    assert ('Préfère un indice avant la réponse.' in question) is enabled
    # The note is not stored in the student's message.
    from backend.app.models import Message, SenderType
    stored = await pg_session.scalar(select(Message.content).where(
        Message.session_id == chat.id, Message.sender == SenderType.user))
    assert stored == 'Exercice 2 ?'
