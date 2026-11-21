"""test_attachments_agent.py — what a question attaches reaches the research loop."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.agent import attachments as at
from backend.app.agent.course_tools import CourseTools
from backend.app.agent.model import ModelReply
from backend.app.agent.runner import ResearchRunner
from backend.app.config import settings
from backend.app.models import ChatFile, Lab, Message, SenderType, Session, UserRole
from backend.app.services import chat_file_service as cfs
from backend.tests.conftest import make_user
from backend.tests.test_course_tools import ViewFixture
from backend.tests.test_runner import Script

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


async def files(db, tmp_path):
    lea = make_user(username='lea')
    db.add(lea)
    await db.flush()
    root = str(tmp_path)
    image = await cfs.save(db, root=root, user_id=lea.id, lab_id=None, filename='capture.png', data=PNG)
    code = await cfs.save(db, root=root, user_id=lea.id, lab_id=None, filename='fib.py',
                          data=b'def fib(n):\n    return fib(n + 1)\n')
    long = await cfs.save(db, root=root, user_id=lea.id, lab_id=None, filename='notes.txt',
                          data=('x' * (at.ATTACH_TEXT_CHARS + 50)).encode())
    scan = ChatFile(user_id=lea.id, filename='scan.pdf', mime='application/pdf', size=10, kind='pdf',
                    storage_key='none', has_text=False)
    db.add(scan)
    earlier = Session(user_id=lea.id, title='Avant')
    db.add(earlier)
    await db.flush()
    db.add_all([Message(session_id=earlier.id, sender=SenderType.user, content='ancienne version',
                        superseded_at=T0, created_at=T0),
                Message(session_id=earlier.id, sender=SenderType.user, content='Pourquoi n-1 ?',
                        created_at=T0 + timedelta(minutes=1)),
                Message(session_id=earlier.id, sender=SenderType.llm, content='Pour se rapprocher du cas de base.',
                        created_at=T0 + timedelta(minutes=2))])
    await db.commit()
    return image, code, long, scan, earlier


def ref(kind, row, name):
    return {'kind': kind, 'id': str(row.id), 'name': name}


@pytest.mark.asyncio
async def test_each_kind_of_attachment(db_session, tmp_path):
    image, code, long, scan, earlier = await files(db_session, tmp_path)
    block, images, docs = await at.build(db_session, [
        ref('file', image, 'capture.png'), ref('file', code, 'fib.py'), ref('file', long, 'notes.txt'),
        ref('file', scan, 'scan.pdf'), ref('session', earlier, 'Avant'),
        {'kind': 'document', 'id': str(uuid.uuid4()), 'name': 'TP3_enonce.pdf'}], storage_root=str(tmp_path))
    assert images and images[0].startswith('data:image/png;base64,')
    assert '<file name="fib.py">\ndef fib(n):\n    return fib(n + 1)\n</file>' in block
    assert f'[Cut: the file has {at.ATTACH_TEXT_CHARS + 50} characters.' in block
    assert at.NO_TEXT_NOTE in block
    assert 'student: Pourquoi n-1 ?\n\nassistant: Pour se rapprocher du cas de base.' in block
    assert 'ancienne version' not in block                         # replaced versions are left out
    assert 'search only in these course files: TP3_enonce.pdf' in block and docs == ['TP3_enonce.pdf']
    assert block.endswith(at.ATTACHED_NOTE)
    assert await at.build(db_session, None, storage_root=str(tmp_path)) == ('', [], [])


@pytest.mark.asyncio
async def test_the_loop_sends_images_as_parts_of_the_question():
    model = Script('Il manque le cas de base.')
    await ResearchRunner(CourseTools(ViewFixture()), model).run(
        'Pourquoi cette erreur ?', attachment_context='<attachments>x</attachments>',
        images=['data:image/png;base64,AAAA'])
    content = model.seen[0][-1]['content']
    assert content[0]['type'] == 'text' and content[0]['text'].startswith('Pourquoi cette erreur ?\n\n<attachments>')
    assert content[1] == {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}}


@pytest.mark.asyncio
async def test_the_chat_sends_the_attached_screenshot_to_the_model(pg_session, tmp_path, monkeypatch):
    from backend.tests.conftest import make_client
    from backend.tests.test_unified_entry import _chat
    rev, student, chat, seen = await _chat(pg_session, monkeypatch, tmp_path,
                                           [ModelReply('Il manque le cas de base.', [], 'fixture', 50, 20)])
    monkeypatch.setattr(settings, 'DOCUMENTS_STORAGE_ROOT', str(tmp_path))
    async with await make_client(pg_session, student) as client:
        up = await client.post('/api/student/files', data={'lab_id': str(chat.lab_id)},
                               files={'file': ('capture.png', PNG)})
        assert up.status_code == 201, up.text
        response = await client.post('/api/chat/stream', json={
            'session_id': str(chat.id), 'message': 'Pourquoi cette erreur ?', 'mode': 'search',
            'attachments': [{'kind': 'file', 'id': up.json()['id']}], 'client_request_id': str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    question = seen[0][-1]['content']
    assert isinstance(question, list) and question[1]['type'] == 'image_url'
    assert '<file name="capture.png">(image, attached below)</file>' in question[0]['text']
    stored = await pg_session.scalar(select(Message).where(Message.session_id == chat.id,
                                                           Message.sender == SenderType.user))
    assert stored.content == 'Pourquoi cette erreur ?' and stored.attachments[0]['name'] == 'capture.png'
