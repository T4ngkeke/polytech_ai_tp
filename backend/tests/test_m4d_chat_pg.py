"""M4-D product HTTP path on a disposable PostgreSQL database."""
import json
import re
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent.model import ModelReply
from backend.app.config import settings
from backend.app.main import app
from backend.app.models import (
    AgentRun, DocumentBlock, DocumentPage, Message, MessageCitation, ModelCallUsage, SenderType, SystemConfig,
)
from backend.app.routers.teacher import get_storage_root
from backend.tests.conftest import make_client
from backend.tests.test_m4_tools_pg import _indexed_pdf


def models(base_url, vision_url=None):
    """The research model (AGENT_*) and the vision model (VLM_*), by default on one endpoint."""
    return [SystemConfig(key='AGENT_BASE_URL', value=base_url), SystemConfig(key='AGENT_MODEL', value='ornith'),
            SystemConfig(key='VLM_BASE_URL', value=vision_url or base_url),
            SystemConfig(key='VLM_MODEL', value='ornith')]


def local_models():
    """The opt-in real-model tests: M4D_AGENT_URL (default local Ornith), and
    M4D_VLM_URL, by default the same model."""
    import os
    agent = os.environ.get('M4D_AGENT_URL', 'http://127.0.0.1:18001/v1')
    return models(agent, os.environ.get('M4D_VLM_URL', agent))


def tool_call(name, arguments):
    return ModelReply('', [{'id': f'{name}-{uuid.uuid4().hex[:6]}', 'name': name,
                            'arguments': json.dumps(arguments)}], 'fixture', 50, 20)


def events(raw):
    parsed = []
    for frame in raw.split('\n\n'):
        if not frame.strip():
            continue
        lines = frame.splitlines()
        name = next((x[7:] for x in lines if x.startswith('event: ')), 'token')
        data = next((x[6:] for x in lines if x.startswith('data: ')), 'null')
        parsed.append((name, json.loads(data)))
    return parsed


@pytest.mark.asyncio
async def test_research_http_save_reload_select_and_pdf(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, rev, teacher, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        page = await db.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id))
        block = await db.scalar(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev.id, DocumentBlock.kind == 'text'))
        assert block is not None
        db.add_all(models('http://example.invalid/v1'))
        await db.commit()
        import backend.app.routers.chat as chat_api
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        async def router(_messages):
            return '{"route":"rag"}'
        monkeypatch.setattr(chat_api, '_make_router_llm_fn', lambda _config: router)
        steps = [
            tool_call('search', {'query': 'Définition θ₁'}),
            tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
            ModelReply('Voir la définition [E1].', [], 'fixture', 50, 20),
        ]
        model_messages = []
        class FakeModel:
            def __init__(self, endpoint):
                pass
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
                model_messages.append(messages)
                return steps.pop(0)
        monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
        client = await make_client(db, student)
        request_id = str(uuid.uuid4())
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Où est définie θ₁ ?',
                'client_request_id': request_id})
            assert response.status_code == 200, response.text
            stream = events(response.text)
            assert [name for name, _ in stream][-3:] == ['token', 'citations', 'done']
            citations = next(payload for name, payload in stream if name == 'citations')
            assert len(citations) == 1 and citations[0]['available']
            assert citations[0]['pdf_page_no'] == page.preview_page_no
            assert citations[0]['href'].startswith(f'/source/{doc.id}?')
            assert await db.scalar(select(MessageCitation).where(
                MessageCitation.id == uuid.UUID(citations[0]['citation_id'])))
            history = await client.get(f'/api/student/sessions/{chat.id}')
            assert history.status_code == 200, history.text
            assistant = next(x for x in history.json()['messages'] if x['sender'] == 'llm')
            assert assistant['citations'][0]['citation_id'] == citations[0]['citation_id']
            selected = await client.post(
                f"/api/chat/citations/{citations[0]['citation_id']}/select",
                params={'session_id': str(chat.id)}, json={})
            assert selected.status_code == 200, selected.text
            pdf = await client.get(f'/api/documents/{doc.id}/pdf', params={
                'session_id': str(chat.id), 'revision_id': str(rev.id)})
            assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
            steps.append(ModelReply('Which definition do you mean?', [], 'fixture', 50, 20))
            followup = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'And this page?',
                'client_request_id': str(uuid.uuid4()),
                'selected_citation_id': citations[0]['citation_id']})
            assert followup.status_code == 200
            assert any(name == 'done' for name, _ in events(followup.text))
            # The selected source reaches the model as a handle it can read.
            note = model_messages[-1][-1]['content']
            assert note.startswith('And this page?') and f'page {page.preview_page_no}: d1.p' in note
            assert citations[0]['citation_id'] not in json.dumps(model_messages[-1])
            # Earlier turns are real messages, without their old evidence IDs.
            assert [m['role'] for m in model_messages[-1][1:]] == ['user', 'assistant', 'user']
            assert '[E1]' not in model_messages[-1][2]['content']
            # The selection applied to that question only.
            steps.append(ModelReply('Autre réponse.', [], 'fixture', 50, 20))
            later = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Another topic?',
                'client_request_id': str(uuid.uuid4())})
            assert any(name == 'done' for name, _ in events(later.text))
            assert 'read it first' not in json.dumps(model_messages[-1])
            # The uncited reply ('Which definition do you mean?') stays in the history.
            assert any(m['content'] == 'Which definition do you mean?' for m in model_messages[-1])
            from backend.app.services.document_cleanup import mark_deleted
            await db.refresh(doc)
            await db.refresh(teacher)
            await mark_deleted(db, document_id=doc.id, teacher_id=teacher.id,
                               expected_generation=doc.generation)
            await db.commit()
            revoked = await client.get(f'/api/student/sessions/{chat.id}')
            assert revoked.status_code == 200
            prior = next(x for x in revoked.json()['messages']
                         if x['id'] == assistant['id'])
            assert prior['content'] == assistant['content']
            assert not prior['citations'][0]['available']
            assert prior['citations'][0]['href'] is None
            denied = await client.post(
                f"/api/chat/citations/{citations[0]['citation_id']}/select",
                params={'session_id': str(chat.id)}, json={})
            assert denied.status_code == 404
    finally:
        app.dependency_overrides.pop(get_storage_root, None)

@pytest.mark.asyncio
async def test_direct_http_saved_and_charged_without_router(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        _, _, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        db.add_all(models('http://example.invalid/v1'))
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        import backend.app.routers.chat as chat_api
        async def forbidden_router(_config):
            raise AssertionError('router called')
        monkeypatch.setattr(chat_api, '_make_router_llm_fn', forbidden_router)
        endpoints = []
        class FakeModel:
            def __init__(self, endpoint):
                endpoints.append(endpoint.model)
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
                return ModelReply('Bonjour !', [], 'ornith', 25, 8)
        monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
        client = await make_client(db, student)
        request_id = str(uuid.uuid4())
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Bonjour',
                'client_request_id': request_id})
            assert response.status_code == 200, response.text
            stream = events(response.text)
            assert ('token', 'Bonjour !') in stream
            assert ('citations', []) in stream
            assert any(name == 'done' for name, _ in stream)
            assert endpoints == ['ornith']
            run = await db.scalar(select(AgentRun).where(AgentRun.session_id == chat.id))
            await db.refresh(run)
            assert run.stop_reason == 'finished'
            calls = (await db.scalars(select(ModelCallUsage).where(
                ModelCallUsage.run_id == run.id))).all()
            assert len(calls) == 1 and {c.model for c in calls} == {'ornith'}
            history = await client.get(f'/api/student/sessions/{chat.id}')
            assert history.status_code == 200
            assert any(m['content'] == 'Bonjour !' and not m['citations']
                       for m in history.json()['messages'])
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_research_history_capped_and_quota_error(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        _, _, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        db.add_all(models('http://example.invalid/v1'))
        db.add_all([SystemConfig(key='AGENT_MAX_TOOL_CALLS', value='12'),
                    SystemConfig(key='AGENT_MAX_INPUT_TOKENS', value='8000')])
        # Ten long earlier questions, about 10k estimated tokens in all.
        db.add_all(Message(session_id=chat.id, sender=SenderType.user, content=f'Q{n} ' + 'x' * 4000)
                   for n in range(10))
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        import backend.app.routers.chat as chat_api
        from backend.app.agent.history import estimate_tokens
        model_messages = []
        class FakeModel:
            def __init__(self, endpoint):
                pass
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
                model_messages.append(messages)
                return ModelReply('Bonjour !', [], 'ornith', 25, 8)
        monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Bonjour',
                'client_request_id': str(uuid.uuid4())})
            assert any(name == 'done' for name, _ in events(response.text))
            first = model_messages[0][1]['content']
            # History may take a quarter of the admin's 8000 input tokens.
            assert 'Q9 ' in first and 'Q0 ' not in first
            assert estimate_tokens(first) <= 2200
            assert 'at most twelve course tool calls' in model_messages[0][0]['content']
            # Enough quota for the pre-check, not for one model call.
            used = await client.get('/api/student/usage')
            await db.refresh(student)
            student.daily_token_quota = used.json()['used'] + 100
            await db.commit()
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Encore ?',
                'client_request_id': str(uuid.uuid4())})
            assert ('error', {'detail': 'Daily token quota exceeded'}) in events(response.text)
            run = await db.scalar(select(AgentRun).where(AgentRun.session_id == chat.id,
                                                          AgentRun.question == 'Encore ?'))
            await db.refresh(run)
            assert run.status == 'failed'
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_real_ornith_direct_student_http(pg_session, tmp_path, monkeypatch):
    import os
    if os.environ.get('RUN_M4D_DIRECT') != '1':
        pytest.skip('opt-in local Ornith direct-answer probe')
    try:
        db = pg_session
        _, _, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        db.add_all(local_models())
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Bonjour !',
                'client_request_id': str(uuid.uuid4())}, timeout=90)
            assert response.status_code == 200, response.text
            stream = events(response.text)
            assert not any(name == 'error' for name, _ in stream), stream
            assert ('citations', []) in stream
            assert any(name == 'done' for name, _ in stream)
            answer = ''.join(data for name, data in stream if name == 'token')
            assert answer and '</think>' not in answer
            run = await db.scalar(select(AgentRun).where(AgentRun.session_id == chat.id))
            await db.refresh(run)
            assert run.stop_reason == 'finished'
            calls = (await db.scalars(select(ModelCallUsage).where(
                ModelCallUsage.run_id == run.id))).all()
            assert len(calls) == 1 and {c.model for c in calls} == {'ornith'}
            assert all(c.usage_source == 'actual' for c in calls)
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_real_local_model_student_research_http(pg_session, tmp_path, monkeypatch):
    import os
    if os.environ.get('RUN_M4D_REAL') != '1':
        pytest.skip('opt-in local model probe')
    from backend.app.models import SystemConfig
    try:
        db = pg_session
        doc, rev, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        db.add_all(local_models())
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        # view_image reads the page image from the test's storage.
        monkeypatch.setattr(settings, 'DOCUMENTS_STORAGE_ROOT', str(tmp_path / 'storage'))
        import backend.app.services.research_service as research_service
        original_execute = research_service._RunningTools.execute
        async def logged_execute(self, name, arguments):
            output = await original_execute(self, name, arguments)
            print('TOOL', name, json.dumps(arguments, ensure_ascii=False),
                  json.dumps(output, ensure_ascii=False)[:500])
            return output
        monkeypatch.setattr(research_service._RunningTools, 'execute', logged_execute)
        import backend.app.routers.chat as chat_api
        real_model = chat_api.ResearchModel
        class LoggedModel(real_model):
            async def __call__(self, messages, max_output, timeout, **kwargs):
                try:
                    reply = await super().__call__(messages, max_output, timeout, **kwargs)
                except Exception as exc:
                    print('MODEL_ERROR', type(exc).__name__,
                          getattr(getattr(exc, 'response', None), 'status_code', None),
                          getattr(getattr(exc, 'response', None), 'text', '')[:500])
                    raise
                print('MODEL', reply.content[:700], reply.tool_calls)
                return reply
        monkeypatch.setattr(chat_api, 'ResearchModel', LoggedModel)
        runner_run = research_service.ResearchRunner.run
        async def logged_run(self, *args, **kwargs):
            result = await runner_run(self, *args, **kwargs)
            print('TRACE', json.dumps(result.trace, ensure_ascii=False)[:2400])
            return result
        monkeypatch.setattr(research_service.ResearchRunner, 'run', logged_run)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id),
                'message': 'Où est expliquée la définition θ₁ dans ce cours ?',
                'client_request_id': str(uuid.uuid4())}, timeout=90)
            assert response.status_code == 200, response.text
            stream = events(response.text)
            assert not any(name == 'error' for name, _ in stream), stream
            answer = ''.join(payload for name, payload in stream if name == 'token')
            citations = next(payload for name, payload in stream if name == 'citations')
            assert answer and any(name == 'done' for name, _ in stream)
            run = await db.scalar(select(AgentRun).where(AgentRun.session_id == chat.id))
            await db.refresh(run)
            print(json.dumps({'answer': answer, 'citations': citations,
                              'stop_reason': run.stop_reason,
                              'events': [name for name, _ in stream]}, ensure_ascii=False))
            assert citations and all(c['available'] for c in citations)
            history = await client.get(f'/api/student/sessions/{chat.id}')
            assert history.status_code == 200
            assert any(m['citations'] for m in history.json()['messages'])
            # A follow-up on a selected source: earlier turns and the source handle.
            followup = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Et que dit exactement cette page ?',
                'client_request_id': str(uuid.uuid4()),
                'selected_citation_id': citations[0]['citation_id']}, timeout=90)
            stream = events(followup.text)
            assert not any(name == 'error' for name, _ in stream), stream
            print('FOLLOWUP', json.dumps({'answer': ''.join(p for n, p in stream if n == 'token'),
                'citations': [c['pdf_page_no'] for n, p in stream if n == 'citations' for c in p]},
                ensure_ascii=False))
            assert any(name == 'done' for name, _ in stream)
    finally:
        app.dependency_overrides.pop(get_storage_root, None)

@pytest.mark.asyncio
async def test_real_vision_http_observation_and_bill(pg_session, tmp_path, monkeypatch):
    import os
    if os.environ.get('RUN_M4D_VISION') != '1':
        pytest.skip('opt-in local Ornith probe')
    from backend.app.models import SystemConfig, ModelCallUsage
    try:
        db = pg_session
        doc, rev, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        page = await db.scalar(select(DocumentPage).where(
            DocumentPage.revision_id == rev.id,
            DocumentPage.preview_asset_id.is_not(None)).limit(1))
        db.add_all(local_models())
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        monkeypatch.setattr(settings, 'DOCUMENTS_STORAGE_ROOT', str(tmp_path / 'storage'))
        import backend.app.routers.chat as chat_api
        async def router(_messages):
            return '{"route":"rag"}'
        monkeypatch.setattr(chat_api, '_make_router_llm_fn', lambda _config: router)
        steps = [tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
                 tool_call('view_image', {'image': 'a1', 'question': 'What is visible?'})]
        class FakeModel:
            def __init__(self, endpoint):
                pass
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
                if steps:
                    return steps.pop(0)
                # Cite only the visual observation just returned.
                visual = re.search(r'\[(E\d+)\] visual observation', messages[-1]['content'])
                return ModelReply(f'The image is shown [{visual[1]}].', [], 'fixture', 50, 20)
        monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'What is in this figure?',
                'client_request_id': str(uuid.uuid4())}, timeout=90)
            assert response.status_code == 200, response.text
            stream = events(response.text)
            assert not any(name == 'error' for name, _ in stream), stream
            citations = next(payload for name, payload in stream if name == 'citations')
            assert len(citations) == 1 and citations[0]['origin'] == 'live_visual'
            assert citations[0]['range'] is None and citations[0]['available']
            visual_call = await db.scalar(select(ModelCallUsage).where(
                ModelCallUsage.purpose == 'vision'))
            assert visual_call is not None and visual_call.model == 'ornith'
            assert visual_call.usage_source == 'actual' and visual_call.input_tokens > 0
            print(json.dumps({'visual_citation': citations[0],
                              'vision_input_tokens': visual_call.input_tokens,
                              'vision_output_tokens': visual_call.output_tokens}, ensure_ascii=False))
    finally:
        app.dependency_overrides.pop(get_storage_root, None)
