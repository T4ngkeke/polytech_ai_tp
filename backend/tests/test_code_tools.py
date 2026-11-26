"""test_code_tools.py — Python run in the student's browser (ported from Open WebUI execute_code)."""
import asyncio
import base64
import json
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent.tools import code as ct
from backend.app.agent.tools.course import CourseTools
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
    model = Script([('execute_code', {'code': 'print(sum(range(5)))'})], 'La somme vaut 10.')
    tools = browser(ct.CodeTools(USER, None), {'stdout': '10\n', 'stderr': '', 'result': ''})
    result = await ResearchRunner(CourseTools(ViewFixture()), model, code_tools=tools).run('Somme de 0 à 4 ?')
    assert ct.CODE_NOTE in model.seen[0][0]['content'] and ct.NO_CODE_NOTE not in model.seen[0][0]['content']
    assert json.loads(model.seen[1][-1]['content'])['stdout'] == '10\n'
    assert result.answer == 'La somme vaut 10.' and result.code_runs[0]['stdout'] == '10\n'
    # Running Python is research: the answer is not asked whether it is one (CHECK_TURN).
    assert model.purposes == ['agent', 'agent']


@pytest.mark.asyncio
async def test_on_the_server_the_page_only_shows_the_run():
    events, saved = [], []

    async def server(code):                      # python_runner: stdout with a figure
        return {'stdout': f'ok\n{ct.IMAGE_LINE}{base64.b64encode(PNG).decode()}\n', 'stderr': '', 'result': '3'}

    async def save(data, n):
        saved.append(data)
        return 'file-1'
    tools = ct.CodeTools(USER, events.append, save_image=save, server=server)
    reply = json.loads(await tools.call('execute_code', json.dumps({'code': 'print(1 + 2)'})))
    assert reply['stdout'] == 'ok\n[image 1 shown to the student under the code]\n' and reply['result'] == '3'
    # The page gets the code (to show it, not to run it), then what came back.
    assert events[0]['where'] == 'server' and events[0]['code'] == 'print(1 + 2)'
    assert events[1]['id'] == events[0]['id'] and events[1]['output']['result'] == '3'
    assert saved == [PNG] and tools.runs[0]['images'] == ['file-1'] and tools.runs[0]['status'] == 'ok'
    assert tools.note == ct.SERVER_CODE_NOTE and not ct._pending
    # The model is told where the code runs.
    model = Script([('execute_code', {'code': 'print(2)'})], 'Deux.')
    await ResearchRunner(CourseTools(ViewFixture()), model, code_tools=ct.CodeTools(USER, events.append, server=server)).run('Q ?')
    assert ct.SERVER_CODE_NOTE in model.seen[0][0]['content'] and ct.CODE_NOTE not in model.seen[0][0]['content']


@pytest.mark.asyncio
async def test_the_server_runner_client():
    import httpx
    seen = []

    def answer(request):
        seen.append(json.loads(request.content))
        code = seen[-1]['code']
        if code == 'slow':
            return httpx.Response(200, json={'status': 'timeout', 'stdout': '', 'stderr': 'stopped after 60 s', 'result': ''})
        if code == 'down':
            return httpx.Response(502)
        return httpx.Response(200, json={'status': 'ok', 'stdout': '4\n', 'stderr': '', 'result': ''})
    runner = ct.ServerPython('http://runner:8000/', transport=httpx.MockTransport(answer))
    assert await runner('print(4)') == {'stdout': '4\n', 'stderr': '', 'result': ''}
    assert await runner('slow') == {'error': 'stopped after 60 s'}
    assert 'not available' in (await runner('down'))['error']
    assert seen[0] == {'code': 'print(4)'}


@pytest.mark.asyncio
async def test_without_python_the_model_is_told_nothing_runs():
    model = Script('Voici le code, à lancer avec Run.', 'DONE')
    await ResearchRunner(CourseTools(ViewFixture()), model).run('Trace sin(x)')
    assert ct.NO_CODE_NOTE in model.seen[0][0]['content'] and ct.CODE_NOTE not in model.seen[0][0]['content']


@pytest.mark.asyncio
async def test_waiting_for_the_browser_is_not_research_time():
    now = [0.0]
    tools = ct.CodeTools(USER, None)

    def send(event):
        now[0] += 100                            # the browser takes 100 s
        asyncio.get_running_loop().call_soon(ct.deliver, event['id'], USER, {'stdout': 'ok'})
    tools.send = send
    model = Script([('execute_code', {'code': 'print(1)'})], [('execute_code', {'code': 'print(2)'})], 'Fini.')
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
    assert code_event == {'id': call_id, 'code': 'print(fact(5))', 'where': 'browser'}
    done = next(payload for name, payload in stream_events if name == 'done')
    assert done['code_runs'][0]['stdout'].startswith('120\n[image 1 shown')
    answer = await pg_session.scalar(select(Message).where(Message.session_id == chat.id,
                                                           Message.sender == SenderType.llm))
    assert answer.code_runs[0]['code'] == 'print(fact(5))'
    image = await pg_session.get(ChatFile, uuid.UUID(answer.code_runs[0]['images'][0]))
    assert image.kind == 'image' and image.session_id == chat.id and image.user_id == student.id


@pytest.mark.asyncio
async def test_with_a_runner_the_chat_runs_the_code_on_the_server(pg_session, tmp_path, monkeypatch):
    from backend.tests.conftest import make_client
    from backend.tests.test_m4d_chat_pg import events
    from backend.tests.test_unified_entry import _chat
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path, [
        ModelReply('', [{'id': 'c1', 'name': 'execute_code',
                         'arguments': json.dumps({'code': 'print(fact(5))'})}], 'fixture', 50, 20),
        ModelReply('fact(5) vaut 120.', [], 'fixture', 50, 20)])
    ran = []

    class Runner:
        def __init__(self, url):
            ran.append(url)

        async def __call__(self, code):
            return {'stdout': '120\n', 'stderr': '', 'result': ''}
    monkeypatch.setattr(settings, 'PYTHON_RUNNER_URL', 'http://runner:8000')
    monkeypatch.setattr(ct, 'ServerPython', Runner)
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Que vaut fact(5) ?', 'tools': ['code'],
            'client_request_id': str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    stream_events = events(response.text)
    code_event = next(payload for name, payload in stream_events if name == 'code')
    output = next(payload for name, payload in stream_events if name == 'code_output')
    assert code_event['where'] == 'server' and output == {'id': code_event['id'], 'output': {
        'stdout': '120\n', 'stderr': '', 'result': ''}}
    assert ran == ['http://runner:8000'] and ct.SERVER_CODE_NOTE in seen[0][0]['content']
    done = next(payload for name, payload in stream_events if name == 'done')
    assert done['code_runs'][0]['stdout'] == '120\n'
