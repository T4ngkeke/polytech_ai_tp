"""test_compaction.py — the summary of what no longer fits in the history (ported from Open WebUI)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.agent import compaction as cp
from backend.app.agent.model import ModelReply
from backend.app.models import Message, SenderType


def turn(i, text):
    return [{'role': 'user', 'content': f'Q{i} {text}', 'id': str(uuid.uuid4())},
            {'role': 'assistant', 'content': f'R{i} {text}', 'id': str(uuid.uuid4())}]


def test_the_summary_goes_in_front_of_the_first_question():
    history = turn(1, 'récursivité')
    out = cp.with_summary(history, 'Léa a vu le cas de base (TP3.pdf, page 4).')
    assert out[0]['content'].startswith(cp.SUMMARY_OPEN) and out[0]['content'].endswith('Q1 récursivité')
    assert out[1] == history[1] and history[0]['content'] == 'Q1 récursivité'     # not changed in place
    assert cp.with_summary(history, None) == history


def test_the_fallback_is_open_webui_s():
    text = cp.fallback('Avant.', [{'role': 'user', 'content': 'x' * 900}, {'role': 'assistant', 'content': ''}])
    assert text == 'Avant.\n- user: ' + 'x' * 500
    assert len(cp.fallback(None, [{'role': 'user', 'content': 'y' * 500}] * 20)) == cp.FALLBACK_MAX_CHARS


@pytest.mark.asyncio
async def test_the_summary_call_gets_the_previous_summary_and_both_parts():
    seen = []

    async def model(messages, max_output, timeout, *, purpose=''):
        seen.append((messages[0]['content'], purpose))
        return ModelReply('Résumé : récursivité, TP3.pdf page 4.', [], 'fixture', 900, 40)
    out = await cp.summarize(model, 'Ancien résumé.', turn(1, 'cas de base'), turn(2, 'fact(0)'))
    prompt = seen[0][0]
    assert out == 'Résumé : récursivité, TP3.pdf page 4.' and seen[0][1] == 'context_compaction'
    assert 'Ancien résumé.' in prompt and 'USER: Q1 cas de base' in prompt and 'USER: Q2 fact(0)' in prompt
    assert 'never as [E1]-style IDs' in prompt

    async def broken(messages, max_output, timeout, *, purpose=''):
        raise TimeoutError
    assert (await cp.summarize(broken, None, turn(1, 'a'), [])).startswith('- user: Q1 a')


T0 = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_the_chat_summarizes_once_and_reuses_the_checkpoint(pg_session, tmp_path, monkeypatch):
    """A long conversation: the dropped part is summarized, stored on the first
    message still sent, and the next question reuses it without a new call."""
    from backend.tests.conftest import make_client
    from backend.app.models import AgentRun
    import backend.app.routers.chat as chat_api
    from backend.tests.test_unified_entry import _chat
    steps = []
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path, steps)
    # 12 earlier turns of research answers, about 3,000 estimated tokens each.
    for i in range(12):
        q = Message(session_id=chat.id, sender=SenderType.user, content=f'Question {i} ' + 'q' * 3000,
                    created_at=T0 + timedelta(minutes=2 * i))
        a = Message(session_id=chat.id, sender=SenderType.llm, content=f'Réponse {i} ' + 'r' * 6000,
                    created_at=T0 + timedelta(minutes=2 * i + 1))
        pg_session.add_all([q, a])
        await pg_session.flush()
        pg_session.add(AgentRun(user_id=student.id, session_id=chat.id, user_message_id=q.id,
                                assistant_message_id=a.id, question=q.content, status='finished',
                                stop_reason='finished', snapshot_map={str(rev.document_id): str(rev.id)},
                                client_request_id=str(uuid.uuid4())))
    await pg_session.commit()
    monkeypatch.setattr(chat_api, '_history_budget', lambda context, limits, mode: 20_000)

    def script(answer):
        steps.extend([ModelReply(answer, [], 'fixture', 50, 20), ModelReply('DONE', [], 'fixture', 5, 1)])
    summaries = []
    real_summarize = cp.summarize

    async def counted(model, previous, compacted, recent, timeout=90):
        summaries.append((previous, len(compacted)))
        return 'Résumé des questions 0 à 6.'
    monkeypatch.setattr(cp, 'summarize', counted)
    async with await make_client(pg_session, student) as client:
        for n, text in enumerate(['Et la suite ?', 'Encore une ?']):
            script(f'Réponse nouvelle {n}.')
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': text, 'client_request_id': str(uuid.uuid4())})
            assert response.status_code == 200, response.text
    assert real_summarize is not cp.summarize and len(summaries) == 1 and summaries[0][0] is None
    research = [m for m in seen if m[0]['role'] == 'system']      # the research calls, not title or follow-ups
    first_sent = [m for m in research[0] if m['role'] == 'user'][0]['content']
    assert first_sent.startswith(cp.SUMMARY_OPEN) and 'Résumé des questions 0 à 6.' in first_sent
    # The second question starts with the same summary in front of the same message.
    second = [m for m in research[2] if m['role'] == 'user'][0]['content']
    assert second == first_sent
    stored = (await pg_session.scalars(select(Message).where(Message.context_summary.is_not(None)))).all()
    assert len(stored) == 1 and stored[0].sender == SenderType.user and first_sent.endswith(stored[0].content)
