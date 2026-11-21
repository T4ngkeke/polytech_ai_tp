"""test_chat_steps.py — what the new chat page needs: the step events of the
process line, and the lab's course files for the attachment menu."""
import uuid

import pytest
from sqlalchemy import select

from backend.app.agent.model import ModelReply
from backend.app.agent.runner import step_of
from backend.app.models import Class, DocumentPage, Lab
from backend.tests.conftest import make_client
from backend.tests.test_m4d_chat_pg import events, tool_call
from backend.tests.test_unified_entry import _chat


def test_a_tool_call_is_described_in_a_few_fields():
    assert step_of('search', '{"query": "cas de base"}', '6 locations (leads, not evidence)\n…') == \
        {'tool': 'search', 'query': 'cas de base', 'count': 6, 'ok': True}
    assert step_of('read', '{"page": "d1.p4"}', 'd1.p4 · TP3_enonce.pdf · page 4 of 6 · previous d1.p3') == \
        {'tool': 'read', 'file': 'TP3_enonce.pdf', 'page': 4, 'ok': True}
    assert step_of('search_web', '{"query": "python"}', '[{"id": "W1"}, {"id": "W2"}]')['count'] == 2
    assert step_of('execute_code', '{}', '{"status": "success", "stdout": "1", "stderr": ""}') == \
        {'tool': 'execute_code', 'ok': True}
    assert step_of('execute_code', '{}', '{"error": "timeout"}')['ok'] is False
    assert step_of('create_tasks', '{}', 'ok') is None
    assert step_of('read', 'not json', 'error: invalid_arguments') == {'tool': 'read', 'file': None, 'page': None, 'ok': False}


@pytest.mark.asyncio
async def test_the_stream_sends_a_step_per_tool_call(pg_session, tmp_path, monkeypatch):
    steps = []
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path, steps)
    page = await pg_session.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id))
    steps += [tool_call('search', {'query': 'Définition θ₁'}),
              tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
              ModelReply('Voir la définition [E1].', [], 'fixture', 50, 20)]
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Où est définie θ₁ ?', 'client_request_id': str(uuid.uuid4())})
    found = [payload for name, payload in events(response.text) if name == 'step']
    assert [s['tool'] for s in found] == ['search', 'read']
    assert found[0]['query'] == 'Définition θ₁' and found[1]['page'] == page.preview_page_no and found[1]['file']


@pytest.mark.asyncio
async def test_the_lab_course_files_for_the_attachment_menu(pg_session, tmp_path, monkeypatch):
    rev, student, chat, _ = await _chat(pg_session, monkeypatch, tmp_path, [])
    lab = await pg_session.get(Lab, chat.lab_id)
    async with await make_client(pg_session, student) as client:
        listed = (await client.get(f'/api/student/labs/{lab.id}/documents')).json()
        assert [d['id'] for d in listed] == [str(rev.document_id)] and listed[0]['filename']
        # In exam mode only the files allowed in the exam are listed.
        cls = await pg_session.get(Class, lab.class_id)
        cls.exam_mode = True
        await pg_session.commit()
        assert (await client.get(f'/api/student/labs/{lab.id}/documents')).json() == []
        assert (await client.get(f'/api/student/labs/{uuid.uuid4()}/documents')).status_code == 404
    # The class's teacher sees the same menu in the test drive; another user does not.
    from backend.app.models import User, UserRole
    from backend.tests.conftest import make_user
    teacher = await pg_session.get(User, cls.teacher_id)
    stranger = make_user(role=UserRole.teacher, username='stranger')
    pg_session.add(stranger)
    await pg_session.commit()
    async with await make_client(pg_session, teacher) as client:
        assert (await client.get(f'/api/student/labs/{lab.id}/documents')).status_code == 200
    async with await make_client(pg_session, stranger) as client:
        assert (await client.get(f'/api/student/labs/{lab.id}/documents')).status_code == 403


@pytest.mark.asyncio
async def test_the_audit_gets_the_citations_of_an_opened_conversation(pg_session, tmp_path, monkeypatch):
    from backend.app.models import User
    steps = []
    rev, student, chat, _ = await _chat(pg_session, monkeypatch, tmp_path, steps)
    page = await pg_session.scalar(select(DocumentPage).where(DocumentPage.revision_id == rev.id))
    steps += [tool_call('read', {'page': f'd1.p{page.preview_page_no}'}),
              ModelReply('Voir la définition [E1].', [], 'fixture', 50, 20)]
    async with await make_client(pg_session, student) as client:
        await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Où est définie θ₁ ?', 'client_request_id': str(uuid.uuid4())})
    lab_id, chat_id = chat.lab_id, chat.id
    pg_session.expire_all()                  # the test shares the app's database session
    lab = await pg_session.get(Lab, lab_id)
    teacher = await pg_session.get(User, (await pg_session.get(Class, lab.class_id)).teacher_id)
    async with await make_client(pg_session, teacher) as client:
        listed = (await client.get(f'/api/teacher/chat-history?lab_id={lab.id}')).json()
        opened = (await client.get(f'/api/teacher/chat-history?session_id={chat_id}')).json()
    def answer(rows):
        row = next(r for r in rows if r['id'] == str(chat_id))
        return next(m for m in row['messages'] if m['sender'] == 'llm')
    assert answer(listed)['citations'] == []                    # the list stays light
    assert [c['label'] for c in answer(opened)['citations']] == ['E1']


@pytest.mark.asyncio
async def test_the_saved_question_id_comes_before_the_answer(pg_session, tmp_path, monkeypatch):
    """The page needs the question's id for a new version even when the answer
    never finishes (an error, the Stop button)."""
    from backend.app.models import Message, SenderType
    steps = []
    rev, student, chat, _ = await _chat(pg_session, monkeypatch, tmp_path, steps)
    steps.append(ModelReply('Bonjour !', [], 'fixture', 50, 20))
    async with await make_client(pg_session, student) as client:
        ok = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Bonjour', 'client_request_id': str(uuid.uuid4())})
    names = [name for name, _ in events(ok.text)]
    question = next(p for n, p in events(ok.text) if n == 'question')
    done = next(p for n, p in events(ok.text) if n == 'done')
    # Sent as soon as the run starts: before any step, draft or answer.
    assert names.index('question') < min(names.index(n) for n in ('draft', 'done') if n in names)
    assert question['user_message_id'] == done['user_message_id']
    pg_session.expire_all()
    saved = await pg_session.get(Message, uuid.UUID(question['user_message_id']))
    assert saved.content == 'Bonjour' and saved.sender == SenderType.user


@pytest.mark.asyncio
async def test_a_follow_up_failure_does_not_follow_the_answer_with_an_error(pg_session, tmp_path, monkeypatch):
    import backend.app.routers.chat as chat_api
    steps = [ModelReply('Bonjour !', [], 'fixture', 50, 20)]
    rev, student, chat, _ = await _chat(pg_session, monkeypatch, tmp_path, steps)

    async def broken(*args, **kwargs):
        raise RuntimeError('database gone')
    monkeypatch.setattr(chat_api.agent_follow_ups, 'generate', broken)
    async with await make_client(pg_session, student) as client:
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Bonjour', 'client_request_id': str(uuid.uuid4())})
    names = [name for name, _ in events(response.text)]
    assert 'done' in names and 'error' not in names and 'follow_ups' not in names
