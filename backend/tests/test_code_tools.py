"""test_code_tools.py — Python run in the student's browser (ported from Open WebUI execute_code)."""
import asyncio
import base64
import json
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent import code_tools as ct
from backend.app.agent.course_tools import CourseTools
from backend.app.agent.model import ModelReply
from backend.app.agent.runner import ResearchRunner
from backend.app.config import settings
from backend.app.models import ChatFile, Message, SenderType
from backend.tests.test_course_tools import ViewFixture
from backend.tests.test_runner import Script

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
USER = uuid.uuid4()


def browser(tools: ct.CodeTools, output: dict, user=USER):
    """The send() of a CodeTools whose browser answers right away."""
    def send(event):
        asyncio.get_running_loop().call_soon(ct.deliver, event['id'], user, output)
    tools.send = send
    return tools


def test_code_from_the_model_is_cleaned():
    assert ct.sanitize_code('```python\nprint(1)\n```') == 'print(1)'
    assert ct.sanitize_code('\x1b[31mprint(2)\x1b[0m') == 'print(2)'


@pytest.mark.asyncio
async def test_a_run_returns_the_browser_output_and_keeps_it():
    sent, saved = [], []

    async def save(data, n):
        saved.append((data, n))
        return f'file-{n}'
    tools = ct.CodeTools(USER, sent.append, save_image=save)
    browser(tools, {'stdout': 'fact(5) = 120\n' + ct.IMAGE_LINE + base64.b64encode(PNG).decode(),
                    'stderr': '', 'result': 'None'})
    reply = json.loads(await tools.call('execute_code', json.dumps({'code': '```py\nprint(fact(5))\n```'})))
    assert reply['status'] == 'success' and reply['stdout'].startswith('fact(5) = 120')
    assert '[image 1 shown to the student under the code]' in reply['stdout'] and 'base64' not in reply['stdout']
    assert saved == [(PNG, 1)]
    run = tools.runs[0]
    assert run['code'] == 'print(fact(5))' and run['images'] == ['file-1'] and run['status'] == 'ok'
    assert not ct._pending                       # nothing left waiting


@pytest.mark.asyncio
async def test_an_error_a_timeout_and_the_budget():
    tools = browser(ct.CodeTools(USER, None, max_calls=3, timeout=0.05),
                    {'stdout': '', 'stderr': 'Traceback (most recent call last):\nRecursionError', 'result': ''})
    reply = json.loads(await tools.call('execute_code', json.dumps({'code': 'fact(5)'})))
    assert 'RecursionError' in reply['stderr'] and tools.runs[0]['status'] == 'error'
    tools.send = lambda event: None              # the page was closed
    reply = json.loads(await tools.call('execute_code', json.dumps({'code': 'while True: pass'})))
    assert 'no result within' in reply['error'] and tools.runs[1]['status'] == 'timeout'
    assert 'error' in json.loads(await tools.call('execute_code', '{}'))       # no code
    assert 'At most 3' in json.loads(await tools.call('execute_code', json.dumps({'code': '1'})))['error']
    assert tools.waited >= 0.05


@pytest.mark.asyncio
async def test_only_the_person_who_asked_can_answer():
    tools = browser(ct.CodeTools(USER, None, timeout=0.05), {'stdout': 'forged'}, user=uuid.uuid4())
    reply = json.loads(await tools.call('execute_code', json.dumps({'code': 'print(1)'})))
    assert 'error' in reply and tools.runs[0]['stdout'] == ''
    assert ct.deliver('unknown', USER, {}) is False


@pytest.mark.asyncio
async def test_the_loop_offers_the_tool_and_keeps_the_runs():
    model = Script([('execute_code', {'code': 'print(sum(range(5)))'})], 'La somme vaut 10.', 'DONE')
    tools = browser(ct.CodeTools(USER, None), {'stdout': '10\n', 'stderr': '', 'result': ''})
    result = await ResearchRunner(CourseTools(ViewFixture()), model, code_tools=tools).run('Somme de 0 à 4 ?')
    assert ct.CODE_NOTE in model.seen[0][0]['content']
    assert json.loads(model.seen[1][-1]['content'])['stdout'] == '10\n'
    assert result.answer == 'La somme vaut 10.' and result.code_runs[0]['stdout'] == '10\n'


@pytest.mark.asyncio
async def test_waiting_for_the_browser_is_not_research_time():
    now = [0.0]
    tools = ct.CodeTools(USER, None)

    def send(event):
        now[0] += 100                            # the browser takes 100 s
        asyncio.get_running_loop().call_soon(ct.deliver, event['id'], USER, {'stdout': 'ok'})
    tools.send = send
    model = Script([('execute_code', {'code': 'print(1)'})], [('execute_code', {'code': 'print(2)'})], 'Fini.', 'DONE')
    course = CourseTools(ViewFixture())
    runner = ResearchRunner(course, model, code_tools=tools, research_timeout=150, total_timeout=200,
                            clock=lambda: now[0])
    # CourseTools keeps its own clock (time.monotonic); this test only checks the runner's.
    result = await runner.run('Exécute deux fois.')
    assert tools.calls == 2 and result.answer == 'Fini.' and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_the_chat_streams_the_code_and_takes_the_browser_result(pg_session, tmp_path, monkeypatch):
    from backend.tests.conftest import make_client
    from backend.tests.test_m4d_chat_pg import events
    from backend.tests.test_unified_entry import _chat
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path, [
        ModelReply('', [{'id': 'c1', 'name': 'execute_code',
                         'arguments': json.dumps({'code': 'print(fact(5))'})}], 'fixture', 50, 20),
        ModelReply('fact(5) vaut 120.', [], 'fixture', 50, 20), ModelReply('DONE', [], 'fixture', 5, 1)])
    monkeypatch.setattr(settings, 'DOCUMENTS_STORAGE_ROOT', str(tmp_path))
    async with await make_client(pg_session, student) as client:
        stream = asyncio.create_task(client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Que vaut fact(5) ?', 'tools': ['code'],
            'client_request_id': str(uuid.uuid4())}))
        for _ in range(200):
            if ct._pending:
                break
            await asyncio.sleep(0.02)
        call_id = next(iter(ct._pending))
        answered = await client.post(f'/api/chat/code/{call_id}', json={
            'stdout': '120\n' + ct.IMAGE_LINE + base64.b64encode(PNG).decode(), 'stderr': '', 'result': ''})
        assert answered.status_code == 204, answered.text
        again = await client.post(f'/api/chat/code/{call_id}', json={'stdout': 'x'})
        assert again.status_code == 404
        response = await stream
    assert response.status_code == 200, response.text
    stream_events = events(response.text)
    code_event = next(payload for name, payload in stream_events if name == 'code')
    assert code_event == {'id': call_id, 'code': 'print(fact(5))'}
    done = next(payload for name, payload in stream_events if name == 'done')
    assert done['code_runs'][0]['stdout'].startswith('120\n[image 1 shown')
    answer = await pg_session.scalar(select(Message).where(Message.session_id == chat.id,
                                                           Message.sender == SenderType.llm))
    assert answer.code_runs[0]['code'] == 'print(fact(5))'
    image = await pg_session.get(ChatFile, uuid.UUID(answer.code_runs[0]['images'][0]))
    assert image.kind == 'image' and image.session_id == chat.id and image.user_id == student.id
