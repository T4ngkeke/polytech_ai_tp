"""test_message_versions.py — new versions of the last turn (edit, regenerate).

Only the latest question can get a new version. The replaced question and
answer stay (for the audit) with superseded_at; the model's history and the
export leave them out.
"""
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import AgentRun, Class, ClassStudent, Lab, Message, SenderType, Session, UserRole
from backend.app.services.research_runs import ResearchRuns, RunConflict
from tests.conftest import make_client, make_user

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> dict:
    teacher, lea = make_user(role=UserRole.teacher, username='teacher'), make_user(username='lea')
    db_session.add_all([teacher, lea])
    await db_session.flush()
    cls = Class(name='Python', teacher_id=teacher.id, invite_code='VERS01')
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(class_id=cls.id, name='TP3')
    db_session.add(lab)
    await db_session.flush()
    db_session.add(ClassStudent(class_id=cls.id, student_id=lea.id))
    chat = Session(user_id=lea.id, lab_id=lab.id, title='Q')
    db_session.add(chat)
    await db_session.flush()
    q1 = Message(session_id=chat.id, sender=SenderType.user, content='first', created_at=T0)
    a1 = Message(session_id=chat.id, sender=SenderType.llm, content='answer 1', created_at=T0 + timedelta(minutes=1))
    q2 = Message(session_id=chat.id, sender=SenderType.user, content='second', created_at=T0 + timedelta(minutes=2))
    a2 = Message(session_id=chat.id, sender=SenderType.llm, content='answer 2', created_at=T0 + timedelta(minutes=3))
    db_session.add_all([q1, a1, q2, a2])
    await db_session.commit()
    return {'lea': lea, 'chat': chat, 'q1': q1, 'a1': a1, 'q2': q2, 'a2': a2}


async def ask(db_session, seed, version_of):
    async with await make_client(db_session, seed['lea']) as c:
        return await c.post('/api/chat/stream', json={
            'session_id': str(seed['chat'].id), 'message': 'again', 'version_of': str(version_of)})


@pytest.mark.asyncio
async def test_only_the_latest_current_question_gets_a_version(db_session, seed):
    out = await ask(db_session, seed, seed['q1'].id)
    assert (out.status_code, out.json()['detail']) == (409, 'not_latest_question')
    out = await ask(db_session, seed, seed['a2'].id)                 # an answer, not a question
    assert (out.status_code, out.json()['detail']) == (404, 'message_not_found')
    seed['q2'].superseded_at = T0
    await db_session.commit()
    out = await ask(db_session, seed, seed['q2'].id)
    assert (out.status_code, out.json()['detail']) == (404, 'message_not_found')
    # The latest question is accepted at the gate (the test app then stops: no agent).
    seed['q2'].superseded_at = None
    await db_session.commit()
    assert (await ask(db_session, seed, seed['q2'].id)).status_code not in (404, 409)


@pytest.mark.asyncio
async def test_the_student_sees_every_version_with_its_flags(db_session, seed):
    seed['q2'].superseded_at = seed['a2'].superseded_at = T0 + timedelta(minutes=5)
    db_session.add(Message(session_id=seed['chat'].id, sender=SenderType.user, content='second bis',
                           version_of=seed['q2'].id, created_at=T0 + timedelta(minutes=5)))
    await db_session.commit()
    async with await make_client(db_session, seed['lea']) as c:
        messages = (await c.get(f"/api/student/sessions/{seed['chat'].id}")).json()['messages']
        exported = (await c.get('/api/student/export?format=md')).text
    flags = {m['content']: (m['superseded_at'] is not None, m['version_of']) for m in messages}
    assert flags['second'] == (True, None) and flags['answer 2'][0] is True
    assert flags['second bis'] == (False, str(seed['q2'].id))
    assert 'second bis' in exported and 'answer 2' not in exported


# ---------------------------------------------------------------------------
# The run (Postgres)
# ---------------------------------------------------------------------------

async def _finish(db, run_id, answer_text):
    run = await db.get(AgentRun, run_id)
    answer = Message(session_id=run.session_id, sender=SenderType.llm, content=answer_text)
    db.add(answer)
    await db.flush()
    run.assistant_message_id, run.status = answer.id, 'finished'
    await db.commit()
    return run.user_message_id, answer.id


@pytest.mark.asyncio
async def test_a_version_replaces_the_turn_for_the_model(pg_session):
    from backend.tests.test_m4_research_persistence_pg import _setup
    scope, factory = await _setup(pg_session)
    store = ResearchRuns(factory)
    first = await store.begin_run(scope=scope, question='Pourquoi n <= 1 ?', client_request_id='v1')
    q1, a1 = await _finish(pg_session, first.run_id, 'Parce que 0! = 1.')
    second = await store.begin_run(scope=replace(scope, run_id=uuid.uuid4()), question='Pourquoi n <= 1 et pas n == 0 ?',
                                   client_request_id='v2', version=(q1, [q1, a1]))
    q2, _ = await _finish(pg_session, second.run_id, 'Les deux marchent.')
    rows = {m.content: m for m in (await pg_session.scalars(select(Message)
                                   .execution_options(populate_existing=True))).all()}
    assert rows['Pourquoi n <= 1 ?'].superseded_at is not None
    assert rows['Parce que 0! = 1.'].superseded_at is not None
    assert rows['Pourquoi n <= 1 et pas n == 0 ?'].version_of == q1
    history, _, _ = await store.load_context(scope)
    assert [h['content'] for h in history] == ['Pourquoi n <= 1 et pas n == 0 ?', 'Les deux marchent.']
    # The turn about to be replaced is left out too.
    history, _, _ = await store.load_context(scope, exclude_ids=[q2])
    assert history == []
    # A version of a turn that was already replaced is refused.
    with pytest.raises(RunConflict, match='version_conflict'):
        await store.begin_run(scope=replace(scope, run_id=uuid.uuid4()), question='Encore ?',
                              client_request_id='v3', version=(q1, [q1, a1]))
