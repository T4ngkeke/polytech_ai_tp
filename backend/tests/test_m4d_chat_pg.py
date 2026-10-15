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
        # Blank frames and SSE comments (keep-alive) carry no event.
        if not frame.strip() or all(x.startswith(':') for x in frame.splitlines()):
            continue
        lines = frame.splitlines()
        name = next((x[7:] for x in lines if x.startswith('event: ')), 'token')
        data = next((x[6:] for x in lines if x.startswith('data: ')), 'null')
        parsed.append((name, json.loads(data)))
    return parsed


@pytest.mark.asyncio
async def test_research_http_save_reload_followup_and_pdf(pg_session, tmp_path, monkeypatch):
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
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='',
                               on_text=None):
                model_messages.append(messages)
                reply = steps.pop(0)
                # A streamed call sends its text as it is written.
                if on_text and reply.content:
                    for word in reply.content.split(' '):
                        on_text(word + ' ')
                return reply
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
            # The draft comes first; the checked answer follows as the token.
            drafts = ''.join(payload for name, payload in stream if name == 'draft')
            assert drafts.strip() == 'Voir la définition [E1].'
            assert [name for name, _ in stream].index('draft') < [name for name, _ in stream].index('token')
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
            pdf = await client.get(f'/api/documents/{doc.id}/pdf', params={
                'session_id': str(chat.id), 'revision_id': str(rev.id)})
            assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
            # The preview draws the cited block's region over an image of its page.
            box = citations[0]['box']
            assert len(box) == 4 and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1
            image_url = f'/api/documents/{doc.id}/revisions/{rev.id}/pages/{page.preview_page_no}/image'
            image = await client.get(image_url, params={'session_id': str(chat.id)})
            assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
            assert image.headers['cache-control'] == 'private, no-store'
            missing = await client.get(f'/api/documents/{doc.id}/revisions/{rev.id}/pages/999/image',
                                       params={'session_id': str(chat.id)})
            assert missing.status_code == 404
            foreign = await client.get(image_url, params={'session_id': str(uuid.uuid4())})
            assert foreign.status_code == 404
            # A follow-up answered from the history may cite the earlier E1:
            # within a session an ID names one stored source.
            steps.append(ModelReply('Comme dit plus haut [E1].', [], 'fixture', 50, 20))
            followup = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'And this page?',
                'client_request_id': str(uuid.uuid4())})
            stream = events(followup.text)
            assert ('token', 'Comme dit plus haut [E1].') in stream
            cited = next(payload for name, payload in stream if name == 'citations')
            assert [c['label'] for c in cited] == ['E1'] and cited[0]['available']
            assert cited[0]['pdf_page_no'] == page.preview_page_no
            # Earlier turns are real messages and keep their evidence IDs.
            assert [m['role'] for m in model_messages[-1][1:]] == ['user', 'assistant', 'user']
            assert model_messages[-1][2]['content'] == 'Voir la définition [E1].'
            # New research continues the numbering after the cited E1; an ID
            # nobody returned sends the model back to the tools.
            steps.extend([tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
                          ModelReply('Encore [E9].', [], 'fixture', 50, 20),
                          ModelReply('Encore [E2].', [], 'fixture', 50, 20)])
            again = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Read it again?',
                'client_request_id': str(uuid.uuid4())})
            cited = next(payload for name, payload in events(again.text) if name == 'citations')
            assert [c['label'] for c in cited] == ['E2']
            assert 'cites E9, which no tool returned' in model_messages[-1][-1]['content']
            steps.append(ModelReply('Which definition do you mean?', [], 'fixture', 50, 20))
            clarify = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'And the other one?',
                'client_request_id': str(uuid.uuid4())})
            assert any(name == 'done' for name, _ in events(clarify.text))
            steps.append(ModelReply('Autre réponse.', [], 'fixture', 50, 20))
            later = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Another topic?',
                'client_request_id': str(uuid.uuid4())})
            assert any(name == 'done' for name, _ in events(later.text))
            # The uncited reply ('Which definition do you mean?') stays in the history.
            assert any(m['content'] == 'Which definition do you mean?' for m in model_messages[-1])
            # The teacher's audit: which file and page the answers cite, by whom.
            rated = await client.post(f'/api/chat/messages/{assistant["id"]}/feedback',
                                      json={'feedback': 'down'})
            assert rated.status_code == 200, rated.text
            assert (await client.get(f'/api/teacher/labs/{chat.lab_id}/citation-report')).status_code == 403
            teacher_client = await make_client(db, teacher)
            async with teacher_client:
                report = (await teacher_client.get(
                    f'/api/teacher/labs/{chat.lab_id}/citation-report')).json()
                empty = (await teacher_client.get(f'/api/teacher/labs/{chat.lab_id}/citation-report',
                                                  params={'start': '2000-01-01', 'end': '2000-01-31'})).json()
                backwards = await teacher_client.get(f'/api/teacher/labs/{chat.lab_id}/citation-report',
                                                     params={'start': '2000-01-31', 'end': '2000-01-01'})
            assert report['totals'] == {'questions': 5, 'students': 1, 'answers_with_sources': 3,
                                        'unverified': 0, 'marked_wrong': 1, 'questions_back': 0}
            [file] = report['files']
            assert file['document_id'] == str(doc.id) and file['in_lab'] and file['share'] == 1.0
            assert (file['answers'], file['students']) == (3, 1)
            [cited_page] = file['pages']
            assert cited_page['pdf_page_no'] == page.preview_page_no and cited_page['passages']
            assert cited_page['passages'][0]['excerpt']
            asked = {e['question'] for e in cited_page['examples']}
            assert 'Où est définie θ₁ ?' in asked and {e['student'] for e in cited_page['examples']} == {student.username}
            assert report['uncited_files'] == [] and report['unverified'] == []
            assert report['marked_wrong'][0]['question'] == 'Où est définie θ₁ ?'
            assert empty['totals']['questions'] == 0 and empty['files'] == []
            assert empty['uncited_files'][0]['document_id'] == str(doc.id)
            assert backwards.status_code == 422
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
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
                # A reply without research gets one check; DONE keeps it.
                return ModelReply('DONE' if purpose == 'answer_check' else 'Bonjour !', [], 'ornith', 25, 8)
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
            assert sorted(c.purpose for c in calls) == ['agent', 'answer_check']
            assert {c.model for c in calls} == {'ornith'}
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
        # Ten long earlier questions with their research answers, about 13k
        # estimated tokens in all.
        from datetime import datetime, timedelta, timezone
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        for n in range(10):
            question = Message(session_id=chat.id, sender=SenderType.user, content=f'Q{n} ' + 'x' * 2000,
                               created_at=base + timedelta(minutes=2 * n))
            answer = Message(session_id=chat.id, sender=SenderType.llm, content=f'A{n} ' + 'y' * 2000,
                             created_at=base + timedelta(minutes=2 * n + 1))
            db.add_all([question, answer])
            await db.flush()
            db.add(AgentRun(user_id=student.id, session_id=chat.id, client_request_id=f'history-{n}',
                            question=question.content, status='finished', stop_reason='finished',
                            settlement_status='settled', snapshot_map={},
                            user_message_id=question.id, assistant_message_id=answer.id))
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        import backend.app.routers.chat as chat_api
        from backend.app.agent.history import estimate_tokens
        model_messages = []
        class FakeModel:
            def __init__(self, endpoint):
                pass
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
                model_messages.append(messages)
                return ModelReply('Bonjour !', [], 'ornith', 25, 8)
        monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Bonjour',
                'client_request_id': str(uuid.uuid4())})
            assert any(name == 'done' for name, _ in events(response.text))
            history = model_messages[0][1:-1]
            # History may take a quarter of the admin's 8000 input tokens; it
            # starts with a question and alternates.
            text = json.dumps(history)
            assert 'Q9 ' in text and 'A9 ' in text and 'Q0 ' not in text
            assert sum(estimate_tokens(m['content']) for m in history) <= 2200
            assert [m['role'] for m in history] == ['user', 'assistant'] * (len(history) // 2)
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
async def test_silent_research_sends_keep_alive_comments(pg_session, tmp_path, monkeypatch):
    """A long silent model call must not leave the stream idle (nginx closes
    a proxied stream after 60 s without data)."""
    import asyncio
    try:
        db = pg_session
        _, _, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        db.add_all(models('http://example.invalid/v1'))
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        import backend.app.routers.chat as chat_api
        monkeypatch.setattr(chat_api, 'KEEPALIVE_SECONDS', 0.2)
        class SlowModel:
            def __init__(self, endpoint):
                pass
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
                await asyncio.sleep(1.2)
                return ModelReply('Bonjour !', [], 'ornith', 25, 8)
        monkeypatch.setattr(chat_api, 'ResearchModel', SlowModel)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Bonjour',
                'client_request_id': str(uuid.uuid4())})
        assert response.text.count(': keep-alive\n\n') >= 3
        assert ('token', 'Bonjour !') in events(response.text)
        assert any(name == 'done' for name, _ in events(response.text))
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
            # The greeting and its check (no research, no citation).
            assert [c.purpose for c in calls][:1] == ['agent'] and len(calls) <= 2
            assert {c.model for c in calls} == {'ornith'}
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
            # A follow-up that refers to the earlier answer.
            followup = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Et que dit exactement cette page ?',
                'client_request_id': str(uuid.uuid4())}, timeout=90)
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
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
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


@pytest.mark.asyncio
async def test_a_question_back_is_saved_shown_and_answered_in_the_next_turn(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, rev, teacher, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        page = await db.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id))
        db.add_all(models('http://example.invalid/v1'))
        await db.commit()
        import backend.app.routers.chat as chat_api
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        async def router(_messages):
            return '{"route":"rag"}'
        monkeypatch.setattr(chat_api, '_make_router_llm_fn', lambda _config: router)
        ask = {'question': 'Quelle définition ?', 'options': ['θ₁', 'θ₂']}
        steps = [tool_call('ask_student', ask),
                 tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
                 ModelReply('Voir la définition [E1].', [], 'fixture', 50, 20),
                 ModelReply('Autre chose ?', [], 'fixture', 50, 20), ModelReply('DONE', [], 'fixture', 5, 1)]
        offered, seen = [], []
        class FakeModel:
            def __init__(self, endpoint):
                pass
            async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
                offered.append([t['function']['name'] for t in tools or []])
                seen.append(messages)
                return steps.pop(0)
        monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
        client = await make_client(db, student)
        async with client:
            first = events((await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'la définition ?'})).text)
            assert ('token', 'Quelle définition ?') in first and ('clarification', ask) in first
            assert [n for n, _ in first][-2:] == ['citations', 'done'] and 'ask_student' in offered[0]
            run = await db.scalar(select(AgentRun).where(AgentRun.session_id == chat.id))
            await db.refresh(run)
            assert run.stop_reason == 'clarification'
            stored = (await client.get(f'/api/student/sessions/{chat.id}')).json()['messages']
            assert stored[-1]['clarification'] == ask and stored[-1]['content'] == 'Quelle définition ?'
            # The student picks an option: the model sees the question and its
            # options, and must answer (no second question back in a row).
            second = events((await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'θ₁'})).text)
            assert ('token', 'Voir la définition [E1].') in second and 'clarification' not in [n for n, _ in second]
            assert 'ask_student' not in offered[1]
            assert {'role': 'assistant', 'content': 'Quelle définition ?\nOptions: θ₁ | θ₂'} in seen[1]
            # After an answer, the tool is offered again.
            await client.post('/api/chat/stream', json={'session_id': str(chat.id), 'message': 'Merci'})
            assert 'ask_student' in offered[3]
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


async def _mode_chat(db, tmp_path, monkeypatch, replies):
    """A student chat whose model records each call and returns `replies` in order."""
    doc, rev, teacher, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
    db.add_all(models('http://example.invalid/v1'))
    await db.commit()
    import backend.app.routers.chat as chat_api
    monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
    calls = []
    class FakeModel:
        def __init__(self, endpoint):
            pass
        async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
            calls.append({'purpose': purpose, 'tools': tools, 'messages': messages})
            return ModelReply(replies.pop(0), [], 'ornith', 40, 10)
    monkeypatch.setattr(chat_api, 'ResearchModel', FakeModel)
    return doc, student, chat, calls


@pytest.mark.asyncio
async def test_search_mode_answers_from_found_pages_and_records_the_mode(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        _, student, chat, calls = await _mode_chat(db, tmp_path, monkeypatch, ['Voir [E1].'])
        client = await make_client(db, student)
        async with client:
            stream = events((await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Définition θ₁', 'mode': 'search'})).text)
            assert ('status', {'route': 'agent', 'phase': 'thinking', 'mode': 'search',
                               'exam_mode': False}) in stream
            assert ('token', 'Voir [E1].') in stream
            cited = next(data for name, data in stream if name == 'citations')
            assert [c['label'] for c in cited] == ['E1']
            # One answer call without tools, the found page in the message.
            assert [c['purpose'] for c in calls] == ['search_answer'] and calls[0]['tools'] is None
            assert '[E1]' in calls[0]['messages'][-1]['content']
            stored = (await client.get(f'/api/student/sessions/{chat.id}')).json()['messages']
            assert stored[-1]['answer_mode'] == 'search' and stored[-1]['citations']
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_direct_mode_reads_nothing_and_cites_nothing(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        _, student, chat, calls = await _mode_chat(db, tmp_path, monkeypatch, ['Une récursion [E3] appelle…'])
        client = await make_client(db, student)
        async with client:
            stream = events((await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'La récursion ?', 'mode': 'direct'})).text)
            # An ID copied from nowhere is removed: no page was read.
            assert ('token', 'Une récursion  appelle…') in stream and ('citations', []) in stream
            assert [c['purpose'] for c in calls] == ['direct_answer'] and calls[0]['tools'] is None
            assert 'Search or Research' in calls[0]['messages'][0]['content']
            stored = (await client.get(f'/api/student/sessions/{chat.id}')).json()['messages']
            assert stored[-1]['answer_mode'] == 'direct'
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_exam_mode_answers_directly_whatever_the_student_asks(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, student, chat, calls = await _mode_chat(db, tmp_path, monkeypatch, ['Réponse.'])
        from backend.app.models import Class
        cls = await db.get(Class, doc.class_id)
        cls.exam_mode = True
        await db.commit()
        client = await make_client(db, student)
        async with client:
            stream = events((await client.post('/api/chat/stream', json={
                'session_id': str(chat.id), 'message': 'Définition θ₁', 'mode': 'research'})).text)
            assert ('status', {'route': 'agent', 'phase': 'thinking', 'mode': 'direct',
                               'exam_mode': True}) in stream
            assert [c['purpose'] for c in calls] == ['direct_answer']
            assert 'exam mode' in calls[0]['messages'][0]['content']
            classes = (await client.get('/api/student/classes')).json()
            assert classes[0]['exam_mode'] is True
    finally:
        app.dependency_overrides.pop(get_storage_root, None)
