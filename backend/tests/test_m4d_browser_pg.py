"""Opt-in real local model -> saved citation -> Firefox PDF click."""
import asyncio
import base64
import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.auth import create_access_token
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import SystemConfig
from backend.app.routers.teacher import get_storage_root
from backend.tests.conftest import make_client
from backend.tests.test_m4_tools_pg import _indexed_pdf

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def webdriver(method, base, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=body,
        headers={'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def browser_check(url, token, expected_page, report_dir):
    port = free_port()
    driver = subprocess.Popen(['/snap/bin/geckodriver', '--port', str(port),
        '--log', 'error'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    session = None
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + '/status', timeout=1)
                break
            except Exception:
                time.sleep(.2)
        session = webdriver('POST', base, '/session', {'capabilities': {
            'alwaysMatch': {'browserName': 'firefox',
                            'moz:firefoxOptions': {'args': ['-headless']}}}})['value']['sessionId']
        webdriver('POST', base, f'/session/{session}/window/rect',
                  {'width': 1366, 'height': 850})
        def js(script, args=None):
            return webdriver('POST', base, f'/session/{session}/execute/sync',
                             {'script': script, 'args': args or []})['value']
        webdriver('POST', base, f'/session/{session}/url', {'url': url + '/login'})
        js("localStorage.setItem('edu_llm_token', arguments[0])", [token])
        webdriver('POST', base, f'/session/{session}/url', {'url': url + '/chat'})
        for _ in range(80):
            if js("return [...document.querySelectorAll('span')].some(x => x.textContent.trim() === 'M2-B')"):
                break
            time.sleep(.15)
        else:
            raise AssertionError('class did not load')
        js("[...document.querySelectorAll('span')].find(x => x.textContent.trim() === 'M2-B').click()")
        for _ in range(60):
            if js("return [...document.querySelectorAll('button')].some(x => x.textContent.trim() === 'TP')"):
                break
            time.sleep(.15)
        else:
            raise AssertionError('lab did not load')
        js("[...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'TP').click()")
        for _ in range(80):
            link = js("return document.querySelector('a[href*=" + json.dumps('/source/') + "]')?.getAttribute('href') || null")
            if link:
                break
            time.sleep(.15)
        assert link and f'page={expected_page}' in link, link
        original = webdriver('GET', base, f'/session/{session}/window/handles')['value'][0]
        screenshot = webdriver('GET', base, f'/session/{session}/screenshot')['value']
        (report_dir / 'chat-citation.png').write_bytes(base64.b64decode(screenshot))
        js("document.querySelector('a[href*=" + json.dumps('/source/') + "]').click()")
        for _ in range(60):
            handles = webdriver('GET', base, f'/session/{session}/window/handles')['value']
            if len(handles) > 1:
                break
            time.sleep(.15)
        assert len(handles) > 1
        new = next(h for h in handles if h != original)
        webdriver('POST', base, f'/session/{session}/window', {'handle': new})
        for _ in range(80):
            opened = js("return {text: document.body?.innerText || '', iframe: document.querySelector('iframe')?.src || ''}") or {'text': '', 'iframe': ''}
            if f'#page={expected_page}' in opened['iframe']:
                break
            time.sleep(.15)
        assert f'#page={expected_page}' in opened['iframe'], opened
        assert f'Opened at PDF page {expected_page}' in opened['text']
        time.sleep(2)  # allow Firefox's PDF viewer to paint before capture
        screenshot = webdriver('GET', base, f'/session/{session}/screenshot')['value']
        (report_dir / 'pdf-page.png').write_bytes(base64.b64decode(screenshot))
        webdriver('POST', base, f'/session/{session}/window', {'handle': original})
        webdriver('POST', base, f'/session/{session}/refresh', {})
        for _ in range(80):
            restored = js("return document.querySelector('a[href*=" + json.dumps('/source/') + "]')?.getAttribute('href') || null")
            if restored:
                break
            time.sleep(.15)
        assert restored and f'page={expected_page}' in restored, restored
        return {'citation_href': link, 'opened_page': expected_page,
                'restored_href': restored, 'firefox': True}
    finally:
        if session:
            try:
                webdriver('DELETE', base, f'/session/{session}')
            except Exception:
                pass
        driver.terminate()
        try:
            driver.wait(timeout=5)
        except subprocess.TimeoutExpired:
            driver.kill()


async def real_course_pdf(db, tmp_path, monkeypatch):
    import hashlib
    from sqlalchemy import select
    from backend.app.models import ClassStudent, Document, DocumentRevision, EMBEDDING_DIM, Session
    from backend.app.services import document_versions
    from backend.tests.conftest import make_user
    from backend.tests.test_structured_ingest import seed
    from backend.worker import structured_ingest as ingest
    from backend.worker.pdf_reader import read_pdf
    from backend.worker.structured_index import process_index_job

    source = ROOT / 'cours/pdf_files/odp_rendered/De_LAlgebre_Relationnel_au_SQL_2022.pdf'
    cache = ROOT / ('documentation/ingestion-agent-acceptance/m3-pdf/'
                    '2026-09-29-m3c/content-list/De_LAlgebre_Relationnel_au_SQL_2022')
    monkeypatch.setattr(settings, 'STRUCTURED_INGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'PDF_STRUCTURED_INGEST_ENABLED', True)
    storage = tmp_path / 'storage'
    storage.mkdir()
    app.dependency_overrides[get_storage_root] = lambda: str(storage)
    teacher, _, lab, _ = await seed(db)
    client = await make_client(db, teacher)
    async with client:
        uploaded = await client.post(f'/api/teacher/labs/{lab.id}/documents',
            files={'file': (source.name, source.read_bytes(), 'application/pdf')})
    assert uploaded.status_code == 201, uploaded.text
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    def cached_page(_source, page_no, _work_dir):
        item = json.loads((cache / f'page-{page_no}.json').read_text())
        assert item['pdf_sha256'] == digest and item['page_no'] == page_no
        return item['content_list']
    def cached_pipeline(path, source_digest, work_dir, *, settings):
        parsed, metrics = read_pdf(path, source_digest, work_dir, cached_page)
        return ingest.ParseOutcome(parsed, {'status': 'source_pdf', 'extractor': metrics},
                                   {'kind': 'same_pdf_physical_page', 'pages': 14}, True)
    monkeypatch.setattr(ingest, 'run_pdf_pipeline', cached_pipeline)
    worker = ingest.StructuredSettings(storage_root=storage, task_root=tmp_path / 'tasks')
    assert await ingest.process_structured_job(db, settings=worker)
    async def embed(texts):
        return [[0.0] * (EMBEDDING_DIM - 1) + [1.0] for _ in texts]
    assert await process_index_job(db, embed_fn=embed)
    doc = await db.get(Document, uuid.UUID(uploaded.json()['id']), populate_existing=True)
    rev = await db.get(DocumentRevision, uuid.UUID(uploaded.json()['revision_id']),
                       populate_existing=True)
    await db.refresh(teacher)
    await document_versions.publish_revision(db, document_id=doc.id, revision_id=rev.id,
        teacher_id=teacher.id, expected_generation=doc.generation, acknowledge_issues=True)
    student = make_user()
    db.add(student)
    await db.flush()
    db.add(ClassStudent(class_id=doc.class_id, student_id=student.id))
    chat = Session(id=uuid.uuid4(), lab_id=doc.lab_id, user_id=student.id)
    db.add(chat)
    await db.commit()
    return doc, rev, student, chat


@pytest.mark.asyncio
async def test_real_model_to_firefox_page_and_refresh(pg_session, tmp_path, monkeypatch):
    if os.environ.get('RUN_M4D_BROWSER') != '1':
        pytest.skip('opt-in Firefox and local model')
    api_server = None
    static_server = None
    task = None
    try:
        db = pg_session
        if os.environ.get('RUN_M4D_REAL_COURSE') == '1':
            doc, rev, student, chat = await real_course_pdf(db, tmp_path, monkeypatch)
            question, expected_page = (
                "Quelle opération SQL correspond à la restriction de l'algèbre relationnelle ?", 9)
        else:
            doc, rev, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
            question, expected_page = ('Où est expliquée la définition θ₁ dans ce cours ?', 1)
        # The research model (AGENT_*) must be served with native tool calls.
        db.add_all([SystemConfig(key='AGENT_BASE_URL', value='http://127.0.0.1:18001/v1'),
                    SystemConfig(key='AGENT_MODEL', value='ornith'),
                    SystemConfig(key='VLM_BASE_URL', value='http://127.0.0.1:18001/v1'),
                    SystemConfig(key='VLM_MODEL', value='ornith')])
        await db.commit()
        monkeypatch.setattr(settings, 'AGENTIC_SEARCH_ENABLED', True)
        client = await make_client(db, student)
        async with client:
            response = await client.post('/api/chat/stream', json={
                'session_id': str(chat.id),
                'message': question,
                'client_request_id': str(uuid.uuid4())}, timeout=90)
        assert response.status_code == 200, response.text
        assert 'event: citations' in response.text and 'event: done' in response.text
        assert f'\"pdf_page_no\": {expected_page}' in response.text, response.text[-1000:]
        report = ROOT / 'documentation/ingestion-agent-acceptance/m4-agent/2026-09-30-m4-patch-browser'
        report.mkdir(parents=True, exist_ok=True)
        (report / 'real-course-sse.txt').write_text(response.text)
        factory = async_sessionmaker(db.bind, expire_on_commit=False)
        async def isolated_db():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_db] = isolated_db
        api_port = free_port()
        api_server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1',
            port=api_port, log_level='error', lifespan='off'))
        task = asyncio.create_task(api_server.serve())
        for _ in range(100):
            if api_server.started:
                break
            await asyncio.sleep(.05)
        assert api_server.started
        dist = ROOT / 'frontend/dist'
        class Frontend(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(dist), **kwargs)
            def log_message(self, *_args):
                pass
            def do_GET(self):
                if self.path.startswith('/api/'):
                    req = urllib.request.Request(f'http://127.0.0.1:{api_port}' + self.path,
                        headers={k: v for k, v in self.headers.items() if k.lower() == 'authorization'})
                    try:
                        with urllib.request.urlopen(req, timeout=30) as reply:
                            data = reply.read()
                            self.send_response(reply.status)
                            self.send_header('Content-Type', reply.headers.get('Content-Type', 'application/octet-stream'))
                            self.send_header('Content-Length', str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                    except urllib.error.HTTPError as exc:
                        self.send_error(exc.code)
                    return
                if self.path == '/' or self.path.startswith('/assets/'):
                    return super().do_GET()
                self.path = '/index.html'
                return super().do_GET()
            def do_POST(self):
                if not self.path.startswith('/api/'):
                    return self.send_error(404)
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                req = urllib.request.Request(f'http://127.0.0.1:{api_port}' + self.path,
                    data=body, method='POST', headers={
                        'Authorization': self.headers.get('Authorization', ''),
                        'Content-Type': self.headers.get('Content-Type', 'application/json')})
                try:
                    with urllib.request.urlopen(req, timeout=30) as reply:
                        data = reply.read()
                        self.send_response(reply.status)
                        self.send_header('Content-Type', reply.headers.get('Content-Type', 'application/json'))
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                except urllib.error.HTTPError as exc:
                    self.send_error(exc.code)
        static_server = ThreadingHTTPServer(('127.0.0.1', free_port()), Frontend)
        static_thread = threading.Thread(target=static_server.serve_forever, daemon=True)
        static_thread.start()
        base = f'http://127.0.0.1:{static_server.server_port}'
        result = await asyncio.to_thread(browser_check, base,
            create_access_token(student.id), expected_page, report)
        (report / 'browser-result.json').write_text(json.dumps({
            'question': question, 'expected_page': expected_page, **result},
            ensure_ascii=False, indent=2) + '\n')
        print('BROWSER', json.dumps(result))
    finally:
        if static_server:
            static_server.shutdown()
            static_server.server_close()
        if api_server:
            api_server.should_exit = True
        if task:
            await task
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_storage_root, None)
