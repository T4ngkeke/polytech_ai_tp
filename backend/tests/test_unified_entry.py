"""test_unified_entry.py — one entry: the model decides whether to search.

Covers how an answer used the course (course_use: cited / not_found / none,
nothing for a question back), the limits of deep research, and the chat
(Postgres, scripted model): a greeting answered without a course tool is
'none'; a deep question gets the deep limits and note and is 'cited'.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent.model import ModelReply
from backend.app.agent.result import ResearchResult, course_use
from backend.app.config import settings
from backend.app.models import DocumentPage, Message, SenderType
from backend.app.services.research_service import DEEP_NOTE, ResearchLimits, deep_limits
from backend.tests.conftest import make_client
from backend.tests.test_m4_tools_pg import _indexed_pdf
from backend.tests.test_m4d_chat_pg import events, models, tool_call


def result(**kw):
    base = dict(answer='x', citations=[], stop_reason='finished', unresolved=[], tool_calls=0, model_calls=1)
    return ResearchResult(**{**base, **kw})


def test_how_an_answer_used_the_course():
    assert course_use(result(citations=[{'evidence_id': 'E1'}], tool_calls=2)) == 'cited'
    assert course_use(result(tool_calls=3)) == 'not_found'
    assert course_use(result()) == 'none'
    assert course_use(result(clarification={'question': 'Lequel ?', 'options': ['a', 'b']})) is None


def test_deep_limits_add_room_within_the_admin_maximums():
    deep = deep_limits(ResearchLimits())
    assert (deep.tool_calls, deep.visual_calls, deep.research_seconds, deep.total_seconds) == (40, 8, 600, 840)
    capped = deep_limits(ResearchLimits(tool_calls=38, total_seconds=1200, research_seconds=1000))
    assert (capped.tool_calls, capped.total_seconds, capped.research_seconds) == (40, 1800, 1800)


async def _chat(db, monkeypatch, tmp_path, steps):
    doc, rev, teacher, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
    db.add_all(models('http://example.invalid/v1'))
    await db.commit()
    import backend.app.routers.chat as chat_api
    monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
    seen = []

    class FakeModel:
        def __init__(self, endpoint):
            pass

        async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
            seen.append(messages)
            return steps.pop(0)
    monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
    return rev, student, chat, seen


@pytest.mark.asyncio
async def test_a_greeting_answered_without_the_course_is_marked_none(pg_session, tmp_path, monkeypatch):
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path,
                                           [ModelReply('Bonjour ! Pose ta question.', [], 'fixture', 50, 20)])
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Bonjour', 'client_request_id': str(uuid.uuid4())})
    done = next(payload for name, payload in events(response.text) if name == 'done')
    assert done['course_use'] == 'none'
    answer = await pg_session.scalar(select(Message).where(Message.session_id == chat.id,
                                                           Message.sender == SenderType.llm))
    assert answer.course_use == 'none'


@pytest.mark.asyncio
async def test_deep_research_gets_more_room_and_the_note(pg_session, tmp_path, monkeypatch):
    steps = []
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path, steps)
    page = await pg_session.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id))
    steps += [tool_call('search', {'query': 'Définition θ₁'}),
              tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
              ModelReply('Voir la définition [E1].', [], 'fixture', 50, 20)]
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Où est définie θ₁ ?', 'mode': 'search',
            'tools': ['deep'], 'client_request_id': str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    system = seen[0][0]['content']
    assert DEEP_NOTE in system and 'at most 40 course tool calls' in system
    done = next(payload for name, payload in events(response.text) if name == 'done')
    assert done['course_use'] == 'cited'
