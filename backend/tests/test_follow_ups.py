"""test_follow_ups.py — suggested next questions (ported from Open WebUI)."""
import json
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent import follow_ups as fu
from backend.app.agent.model import ModelReply
from backend.app.models import Message, SenderType


def test_replies_are_checked_like_open_webui():
    reply = 'Voici : {"follow_ups": ["Pourquoi n-1 ?", " Pourquoi n-1 ? ", "", "' + 'x' * 401 + '", "Et fact(0) ?"]}'
    assert fu.parse_follow_ups(reply) == ['Pourquoi n-1 ?', 'Et fact(0) ?']
    assert fu.parse_follow_ups('{"follow_ups": [1, "a"]}') == []
    assert fu.parse_follow_ups('pas de json') == [] and fu.parse_follow_ups(None) == []
    assert len(fu.parse_follow_ups(json.dumps({'follow_ups': [str(i) for i in range(9)]}))) == 5


def test_the_history_keeps_the_first_question_and_the_last_four():
    messages = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'm{i}'} for i in range(8)]
    messages[-1]['content'] = 'A' * 3000
    text = fu.chat_history(messages)
    assert text.split('\n')[0] == 'USER: m0' and 'USER: m4' in text and 'm2' not in text
    assert len(text.split('\n')[-1]) == len('ASSISTANT: ') + 1000 and '...' in text


@pytest.mark.asyncio
async def test_the_rules_go_in_and_a_failure_gives_nothing():
    seen = []

    async def model(messages, max_output, timeout, *, purpose=''):
        seen.append((messages[0]['content'], purpose))
        return ModelReply('{"follow_ups": ["Un indice pour la question 2 ?"]}', [], 'fixture', 100, 20)
    out = await fu.generate(model, [{'role': 'user', 'content': 'Q1'}, {'role': 'assistant', 'content': 'R1'}],
                            teaching_rules='Ne donne que des indices.')
    assert out == ['Un indice pour la question 2 ?'] and seen[0][1] == 'follow_ups'
    assert '<rules>\nNe donne que des indices.\n</rules>' in seen[0][0] and 'USER: Q1\nASSISTANT: R1' in seen[0][0]

    async def broken(messages, max_output, timeout, *, purpose=''):
        raise TimeoutError
    assert await fu.generate(broken, []) == []


@pytest.mark.asyncio
async def test_the_chat_sends_and_saves_the_suggestions(pg_session, tmp_path, monkeypatch):
    from backend.tests.conftest import make_client
    from backend.tests.test_m4d_chat_pg import events
    from backend.tests.test_unified_entry import _chat
    steps = [ModelReply('Pense au cas de base.', [], 'fixture', 50, 20), ModelReply('DONE', [], 'fixture', 5, 1),
             ModelReply('Récursivité', [], 'fixture', 5, 2),                   # the title
             ModelReply('{"follow_ups": ["Et pour fact(0) ?", "Peux-tu vérifier mon code ?"]}', [], 'fixture', 9, 9)]
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path, steps)
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Pourquoi ma récursion ne s\'arrête pas ?',
            'client_request_id': str(uuid.uuid4())})
        assert response.status_code == 200, response.text
        stream = events(response.text)
        assert [name for name, _ in stream][-1] == 'follow_ups'
        sent = stream[-1][1]
        assert sent['follow_ups'] == ['Et pour fact(0) ?', 'Peux-tu vérifier mon code ?']
        history = (await client.get(f'/api/student/sessions/{chat.id}')).json()
    answer = next(m for m in history['messages'] if m['sender'] == 'llm')
    assert answer['id'] == sent['message_id'] and answer['follow_ups'] == sent['follow_ups']
    stored = await pg_session.scalar(select(Message.follow_ups).where(
        Message.session_id == chat.id, Message.sender == SenderType.llm))
    assert stored == sent['follow_ups']
