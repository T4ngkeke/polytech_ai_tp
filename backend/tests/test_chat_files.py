"""test_chat_files.py — files students upload in the chat, and attachments.

Covers reading a file (images, PDF text layer, scanned PDF, text, refused
types), the upload / list / hide / download routes and who may use them,
the chat refusing attachments it may not use, and a started run saving the
attachments on the student's message.
"""
import uuid

import pymupdf
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.config import settings
from backend.app.models import ChatFile, Class, ClassStudent, Lab, Message, Session, UserRole
from backend.app.services import chat_file_service as cfs
from backend.app.services.research_runs import ResearchRuns
from tests.conftest import make_client, make_user

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def pdf(text: str | None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


# ---------------------------------------------------------------------------
# Reading a file
# ---------------------------------------------------------------------------

def test_reading_each_kind_of_file():
    assert cfs.read_file('capture.png', PNG) == ('image', 'image/png', None)
    kind, mime, text = cfs.read_file('tp.pdf', pdf('Le cas de base arrete la recursion'))
    assert (kind, mime) == ('pdf', 'application/pdf') and text.startswith('[page 1]') and 'cas de base' in text
    assert cfs.read_file('scan.pdf', pdf(None))[2] == ''                       # no text layer
    assert cfs.read_file('ex3.py', 'def f(n):\n    return 1  # é\n'.encode()) == (
        'text', 'text/plain', 'def f(n):\n    return 1  # é\n')


@pytest.mark.parametrize('name,data,code', [
    ('a.py', b'', 'empty_file'),
    ('a.py', b'print(1)\x00\x01', 'unsupported_type'),                       # binary bytes
    ('setup.exe', b'MZ\x90\x00', 'unsupported_type'),
    ('notes.docx', b'PK\x03\x04', 'unsupported_type'),
    ('big.txt', b'a' * (cfs.MAX_BYTES + 1), 'file_too_large'),
    ('broken.pdf', b'%PDF-1.7 not really', 'unreadable_pdf'),
])
def test_refused_files(name, data, code):
    with pytest.raises(cfs.ChatFileError) as err:
        cfs.read_file(name, data)
    assert err.value.code == code


def test_a_name_keeps_only_its_last_part():
    assert cfs.clean_name('../../etc/passwd') == 'passwd'
    assert cfs.clean_name('C:\\Users\\lea\\tp3.py') == 'tp3.py'
    assert cfs.clean_name('') == 'file'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed(db_session: AsyncSession, tmp_path, monkeypatch) -> dict:
    monkeypatch.setattr(settings, 'DOCUMENTS_STORAGE_ROOT', str(tmp_path))
    teacher = make_user(role=UserRole.teacher, username='teacher')
    lea, hugo = make_user(username='lea'), make_user(username='hugo')
    db_session.add_all([teacher, lea, hugo])
    await db_session.flush()
    cls = Class(name='Python', teacher_id=teacher.id, invite_code='FILE01')
    db_session.add(cls)
    await db_session.flush()
    lab, lab2 = Lab(class_id=cls.id, name='TP3'), Lab(class_id=cls.id, name='TP2')
    db_session.add_all([lab, lab2])
    await db_session.flush()
    db_session.add_all([ClassStudent(class_id=cls.id, student_id=lea.id),
                        ClassStudent(class_id=cls.id, student_id=hugo.id)])
    chat = Session(user_id=lea.id, lab_id=lab.id, title='Q')
    earlier = Session(user_id=lea.id, lab_id=lab.id, title='Earlier')
    db_session.add_all([chat, earlier])
    await db_session.commit()
    return {'teacher': teacher, 'lea': lea, 'hugo': hugo, 'class': cls, 'lab': lab, 'lab2': lab2,
            'chat': chat, 'earlier': earlier, 'root': tmp_path}


async def upload(c, lab_id, name='ex3.py', data=b'print("hi")\n'):
    return await c.post('/api/student/files', data={'lab_id': str(lab_id)}, files={'file': (name, data)})


@pytest.mark.asyncio
async def test_upload_list_download_and_hide(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        out = await upload(c, seed['lab'].id)
        assert out.status_code == 201
        body = out.json()
        assert (body['kind'], body['has_text'], body['text_chars']) == ('text', True, 11)
        row = await db_session.get(ChatFile, uuid.UUID(body['id']))
        assert (seed['root'] / row.storage_key).read_bytes() == b'print("hi")\n'
        assert [f['id'] for f in (await c.get('/api/student/files')).json()] == [body['id']]
        got = await c.get(f"/api/files/{body['id']}/content")
        assert got.status_code == 200 and got.content == b'print("hi")\n'
        assert (await c.delete(f"/api/student/files/{body['id']}")).status_code == 204
        assert (await c.get('/api/student/files')).json() == []
        assert (await c.get(f"/api/files/{body['id']}/content")).status_code == 404
    async with await make_client(db_session, seed['teacher']) as c:          # the audit still sees it
        assert (await c.get(f"/api/files/{body['id']}/content")).status_code == 200
    async with await make_client(db_session, seed['hugo']) as c:
        assert (await c.get(f"/api/files/{body['id']}/content")).status_code == 404


@pytest.mark.asyncio
async def test_uploads_follow_the_lab_rules(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        assert (await upload(c, seed['lab'].id, 'a.exe', b'MZ\x90')).status_code == 415
        seed['lab'].feature_overrides = {'upload': 'off'}
        await db_session.commit()
        out = await upload(c, seed['lab'].id)
        assert out.status_code == 403 and out.json()['detail'] == 'feature_off'
        seed['lab'].feature_overrides = {}
        seed['class'].exam_mode = True
        await db_session.commit()
        assert (await upload(c, seed['lab'].id)).status_code == 403
    outsider = make_user(username='outsider')
    db_session.add(outsider)
    await db_session.commit()
    async with await make_client(db_session, outsider) as c:
        seed['class'].exam_mode = False
        await db_session.commit()
        out = await upload(c, seed['lab'].id)
        assert out.status_code == 403 and out.json()['detail'] == 'not_member'


# ---------------------------------------------------------------------------
# Attachments in the chat
# ---------------------------------------------------------------------------

async def ask(db_session, user, session, attachments):
    async with await make_client(db_session, user) as c:
        return await c.post('/api/chat/stream', json={
            'session_id': str(session.id), 'message': 'hi', 'attachments': attachments})


@pytest.mark.asyncio
async def test_the_chat_refuses_attachments_it_may_not_use(db_session, seed):
    async with await make_client(db_session, seed['hugo']) as c:
        hugo_file = (await upload(c, seed['lab'].id)).json()['id']
    async with await make_client(db_session, seed['lea']) as c:
        own_file = (await upload(c, seed['lab'].id)).json()['id']
        other_lab_file = (await upload(c, seed['lab2'].id)).json()['id']
    lea, chat = seed['lea'], seed['chat']
    cases = [
        ([{'kind': 'file', 'id': hugo_file}], 404, 'attachment_unavailable'),
        ([{'kind': 'file', 'id': other_lab_file}], 404, 'attachment_unavailable'),
        ([{'kind': 'document', 'id': str(uuid.uuid4())}], 404, 'attachment_unavailable'),
        ([{'kind': 'session', 'id': str(chat.id)}], 404, 'attachment_unavailable'),   # the chat itself
    ]
    for refs, code, detail in cases:
        out = await ask(db_session, lea, chat, refs)
        assert (out.status_code, out.json()['detail']) == (code, detail), refs
    seed['lab'].feature_overrides = {'upload': 'off'}
    await db_session.commit()
    out = await ask(db_session, lea, chat, [{'kind': 'file', 'id': own_file}])
    assert (out.status_code, out.json()['detail']) == (403, 'feature_off')
    seed['class'].exam_mode = True
    await db_session.commit()
    out = await ask(db_session, lea, chat, [{'kind': 'session', 'id': str(seed['earlier'].id)}])
    assert (out.status_code, out.json()['detail']) == (403, 'exam_mode')
    too_many = [{'kind': 'session', 'id': str(uuid.uuid4())} for _ in range(6)]
    assert (await ask(db_session, lea, chat, too_many)).status_code == 422


@pytest.mark.asyncio
async def test_allowed_attachments_are_resolved_with_their_names(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        own_file = (await upload(c, seed['lab'].id, 'capture.png', PNG)).json()['id']
    allowed = {'upload': True, 'web': True, 'code': True, 'deep': True}
    refs = [cfs_ref('file', own_file), cfs_ref('session', seed['earlier'].id), cfs_ref('file', own_file)]
    out = await cfs.resolve_attachments(
        db_session, refs, user_id=seed['lea'].id, session_id=seed['chat'].id, lab_id=seed['lab'].id,
        class_id=seed['class'].id, exam_mode=False, allowed=allowed)
    assert out == [
        {'kind': 'file', 'id': own_file, 'name': 'capture.png', 'file_kind': 'image', 'has_text': False},
        {'kind': 'session', 'id': str(seed['earlier'].id), 'name': 'Earlier'},
    ]


def cfs_ref(kind, ref_id):
    from backend.app.schemas import AttachmentRef
    return AttachmentRef(kind=kind, id=ref_id)


@pytest.mark.asyncio
async def test_a_started_run_saves_the_attachments(pg_session, tmp_path, monkeypatch):
    from backend.tests.test_m4_research_persistence_pg import _setup
    monkeypatch.setattr(settings, 'DOCUMENTS_STORAGE_ROOT', str(tmp_path))
    scope, factory = await _setup(pg_session)
    row = await cfs.save(pg_session, root=str(tmp_path), user_id=scope.user_id, lab_id=scope.lab_id,
                         filename='ex3.py', data=b'print(1)\n')
    await pg_session.commit()
    attachments = [{'kind': 'file', 'id': str(row.id), 'name': 'ex3.py', 'file_kind': 'text', 'has_text': True}]
    await ResearchRuns(factory).begin_run(scope=scope, question='Why?', client_request_id='req-a',
                                          attachments=attachments)
    message = await pg_session.scalar(select(Message).where(Message.session_id == scope.session_id))
    assert message.attachments == attachments
    await pg_session.refresh(row)
    assert row.session_id == scope.session_id
