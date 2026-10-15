"""
test_session_titles.py — a new conversation is titled from its first question
and answer; a title the student wrote is never replaced.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.agent.model import ModelReply
from backend.app.models import Class, Lab, Message, SenderType, Session, UserRole
from backend.app.services.session_titles import clean_title, title_first_exchange
from backend.tests.conftest import make_user


def test_the_title_is_one_clean_short_line():
    assert clean_title('"Jointure naturelle en SQL."') == 'Jointure naturelle en SQL'
    assert clean_title('Titre : **Parcours d\'arbre**\nautre ligne') == "Parcours d'arbre"
    assert clean_title('\n  \n') is None
    assert len(clean_title('mot ' * 40)) == 60


async def _session(db, *, title=None, answers=1, is_test=False):
    teacher, student = make_user(role=UserRole.teacher), make_user()
    db.add_all([teacher, student])
    await db.flush()
    cls = Class(id=uuid.uuid4(), name='Algo', teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db.add(cls)
    await db.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name='TP')
    db.add(lab)
    await db.flush()
    session = Session(id=uuid.uuid4(), user_id=student.id, lab_id=lab.id, title=title, is_test=is_test)
    db.add(session)
    await db.flush()
    for _ in range(answers):
        db.add_all([Message(session_id=session.id, sender=SenderType.user, content='q'),
                    Message(session_id=session.id, sender=SenderType.llm, content='a')])
    await db.commit()
    return session.id


class FakeModel:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def __call__(self, messages, max_output, timeout, *, purpose=''):
        self.calls.append((messages, purpose))
        if self.error:
            raise self.error
        return ModelReply(content=self.reply)


@pytest.mark.asyncio
async def test_first_answer_titles_the_conversation(db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    session_id = await _session(db_session)
    model = FakeModel('« Différence WHERE et HAVING »')
    title = await title_first_exchange(factory, session_id, model, 'WHERE ou HAVING ?', 'HAVING filtre les groupes.')
    assert title == 'Différence WHERE et HAVING'
    assert model.calls[0][1] == 'title'
    assert 'HAVING filtre les groupes.' in model.calls[0][0][1]['content']
    async with factory() as db:
        assert (await db.get(Session, session_id)).title == title


@pytest.mark.asyncio
async def test_a_failed_model_falls_back_to_the_question(db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    session_id = await _session(db_session)
    title = await title_first_exchange(factory, session_id, FakeModel(error=TimeoutError()),
                                       'Comment marche Dijkstra ?\nmerci', 'Il choisit…')
    assert title == 'Comment marche Dijkstra ?'


@pytest.mark.asyncio
@pytest.mark.parametrize('setup', [dict(title='Mon titre'), dict(answers=2), dict(is_test=True)])
async def test_titled_later_or_test_conversations_are_left_alone(db_session, setup):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    session_id = await _session(db_session, **setup)
    model = FakeModel('Titre')
    assert await title_first_exchange(factory, session_id, model, 'q', 'a') is None
    assert model.calls == []
