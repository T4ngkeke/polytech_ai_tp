"""The MinerU extractor: one process per document, groups of pages, and the
page-by-page retry of a failed group. A small script stands in for
pdf_mineru_cli, so no MinerU is needed."""
import hashlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pymupdf
import pytest

from backend.worker import pdf_mineru
from backend.worker.pdf_reader import read_pdf

# Same arguments as pdf_mineru_cli. FAKE_BAD_GROUP: a page whose group fails;
# FAKE_BAD_ALONE: a page that also fails alone; FAKE_STALL: a page whose group
# never ends. Each run appends its arguments to FAKE_LOG.
FAKE_CLI = r'''
import json, os, sys, time
a = sys.argv[1:]
arg = lambda name: a[a.index(name) + 1] if name in a else None
out = arg('--out')
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps({'page': arg('--page'), 'first': arg('--first'), 'last': arg('--last'),
                          'key': os.environ.get('MINERU_VL_API_KEY')}) + '\n')
bad_group = {int(p) for p in os.environ.get('FAKE_BAD_GROUP', '').split(',') if p}
bad_alone = {int(p) for p in os.environ.get('FAKE_BAD_ALONE', '').split(',') if p}
stall = {int(p) for p in os.environ.get('FAKE_STALL', '').split(',') if p}
blocks = lambda n: [{'type': 'text', 'text': f'page {n}', 'bbox': [100, 100, 900, 200], 'page_idx': 0}]
if arg('--page'):
    n = int(arg('--page'))
    if n in bad_alone:
        sys.exit(1)
    open(os.path.join(out, 'result.json'), 'w').write(json.dumps(blocks(n)))
    sys.exit(0)
first, last = int(arg('--first')), int(arg('--last'))
for start in range(first, last + 1, int(os.environ['FAKE_GROUP'])):
    group = range(start, min(last, start + int(os.environ['FAKE_GROUP']) - 1) + 1)
    if stall & set(group):
        open(os.path.join(out, 'stalled'), 'w').write(str(os.getpid()))
        time.sleep(600)
    if bad_group & set(group):
        sys.exit(1)
    for n in group:
        part = os.path.join(out, f'page-{n}.part')
        open(part, 'w').write(json.dumps(blocks(n)))
        os.replace(part, os.path.join(out, f'page-{n}.json'))
'''


def make_pdf(path: Path, pages: int) -> str:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        doc.new_page().insert_text((70, 80), f'page {n}')
    path.write_bytes(doc.tobytes())
    doc.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script, log = tmp_path / 'fake_cli.py', tmp_path / 'runs.jsonl'
    script.write_text(FAKE_CLI)
    config = tmp_path / 'mineru.json'
    config.write_text('{}')
    monkeypatch.setenv('FAKE_LOG', str(log))
    monkeypatch.setenv('FAKE_GROUP', str(pdf_mineru.PAGES_PER_CALL))
    monkeypatch.delenv('MINERU_VL_API_KEY', raising=False)

    def build(timeout=30, api_key=''):
        extractor = pdf_mineru.subprocess_extractor(python_executable=sys.executable, server_url='http://m:8000/v1',
                                                    config_path=str(config), timeout_seconds=timeout, api_key=api_key)
        extractor.base = [sys.executable, str(script)] + extractor.base[3:]
        return extractor
    build.runs = lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return build


def read(tmp_path, extractor, pages):
    path = tmp_path / 'deck.pdf'
    digest = make_pdf(path, pages)
    try:
        return read_pdf(path, digest, tmp_path / 'work', extractor)[0]
    finally:
        extractor.close()


def texts(doc):
    return [[b.content for b in page.blocks] for page in doc.pages]


def test_one_process_reads_the_whole_document(tmp_path, fake):
    doc = read(tmp_path, fake(api_key='k1'), 40)
    assert texts(doc) == [[f'page {n}'] for n in range(1, 41)]
    assert all(page.state == 'usable' for page in doc.pages)
    assert fake.runs() == [{'page': None, 'first': '1', 'last': '40', 'key': 'k1'}]


def test_a_failed_group_is_read_again_page_by_page(tmp_path, fake, monkeypatch):
    monkeypatch.setenv('FAKE_BAD_GROUP', '20')
    doc = read(tmp_path, fake(), 40)
    assert texts(doc) == [[f'page {n}'] for n in range(1, 41)]
    runs = fake.runs()
    assert runs[0]['first'] == '1'
    assert [int(r['page']) for r in runs[1:17]] == list(range(17, 33))
    assert runs[17]['first'] == '33' and len(runs) == 18
    assert all(r['key'] is None for r in runs)


def test_a_page_that_fails_alone_costs_only_that_page(tmp_path, fake, monkeypatch):
    monkeypatch.setenv('FAKE_BAD_GROUP', '5')
    monkeypatch.setenv('FAKE_BAD_ALONE', '5')
    doc = read(tmp_path, fake(), 20)
    assert [page.state for page in doc.pages].count('partial') == 1
    assert doc.pages[4].state == 'partial'
    assert any(i.code == 'extraction_failure' for i in doc.pages[4].issues)
    assert texts(doc)[5:] == [[f'page {n}'] for n in range(6, 21)]


def test_a_stalled_group_is_stopped(tmp_path, fake, monkeypatch):
    monkeypatch.setenv('FAKE_STALL', '3')
    started = time.monotonic()
    doc = read(tmp_path, fake(timeout=2), 20)
    assert time.monotonic() - started < 30
    assert texts(doc) == [[f'page {n}'] for n in range(1, 21)]
    pid = int(next((tmp_path / 'work').glob('mineru-from-1/stalled')).read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_close_stops_a_process_still_reading(tmp_path, fake, monkeypatch):
    monkeypatch.setenv('FAKE_STALL', '20')
    path = tmp_path / 'deck.pdf'
    make_pdf(path, 20)
    extractor = fake()
    assert extractor(path, 1, tmp_path / 'work' / 'mineru-1') == [
        {'type': 'text', 'text': 'page 1', 'bbox': [100, 100, 900, 200], 'page_idx': 0}]
    process = extractor.process
    extractor.close()
    assert process.poll() is not None and extractor.process is None


def test_extract_pages_splits_one_call_by_page(tmp_path, monkeypatch):
    calls = []

    def do_parse(out, names, data, langs, **options):
        calls.append(options)
        found = Path(out) / 'page' / 'auto'
        found.mkdir(parents=True)
        (found / 'page_content_list.json').write_text(json.dumps([
            {'type': 'text', 'text': 'a', 'page_idx': 0}, {'type': 'text', 'text': 'b', 'page_idx': 2},
            {'type': 'text', 'text': 'c', 'page_idx': 2}]))
    cli = types.ModuleType('mineru.cli.common')
    cli.do_parse = do_parse
    monkeypatch.setitem(sys.modules, 'mineru', types.ModuleType('mineru'))
    monkeypatch.setitem(sys.modules, 'mineru.cli', types.ModuleType('mineru.cli'))
    monkeypatch.setitem(sys.modules, 'mineru.cli.common', cli)
    config = tmp_path / 'mineru.json'
    config.write_text('{}')
    pdf = tmp_path / 'a.pdf'
    pdf.write_bytes(b'%PDF-')
    pages = pdf_mineru.extract_pages(pdf, 5, 7, tmp_path / 'out', server_url='http://m:8000/v1',
                                     config_path=str(config))
    assert [[b['text'] for b in p] for p in pages] == [['a'], [], ['b', 'c']]
    assert all(b['page_idx'] == 0 for p in pages for b in p)
    assert calls[0]['start_page_id'] == 4 and calls[0]['end_page_id'] == 6
    assert calls[0]['image_analysis'] is False and calls[0]['effort'] == 'high'


def test_footnotes_and_references_are_text(tmp_path):
    path = tmp_path / 'notes.pdf'
    digest = make_pdf(path, 1)

    def items(*_):
        return [{'type': kind, 'text': kind, 'bbox': [100, 100, 900, 200], 'page_idx': 0}
                for kind in ('ref_text', 'page_footnote', 'aside_text', 'phonetic')]
    doc, _ = read_pdf(path, digest, tmp_path / 'work', items)
    page = doc.pages[0]
    assert page.state == 'usable'
    assert [(b.kind, b.content) for b in page.blocks] == [
        ('text', 'ref_text'), ('text', 'page_footnote'), ('text', 'aside_text'), ('text', 'phonetic')]


def test_control_characters_are_removed(tmp_path):
    path = tmp_path / 'tex.pdf'
    digest = make_pdf(path, 1)

    def items(*_):
        return [{'type': 'text', 'text': 'function \x00<sup>bk</sup>\x01 and\ttab\nline', 'bbox': [100, 100, 900, 200],
                 'page_idx': 0}]
    doc, _ = read_pdf(path, digest, tmp_path / 'work', items)
    block = doc.pages[0].blocks[0]
    assert block.content == block.source_text == 'function <sup>bk</sup> and\ttab\nline'


# --------------------------------------------------------------------------
# The GPU service (pdf_mineru_gpu) and the worker's use of it.
# --------------------------------------------------------------------------

@pytest.fixture
def gpu_service():
    """A pdf_mineru_gpu server on a free port, with a fake MinerU. Returns
    (url, calls, settings): settings['fail_from'] makes a group from that page fail."""
    import threading
    from http.server import ThreadingHTTPServer
    from backend.worker import pdf_mineru_gpu
    calls, settings = [], {'fail_from': None}
    ready = threading.Event()

    def extract(pdf, first, last, out):
        calls.append((first, last, pdf.read_bytes()[:5]))
        if settings['fail_from'] is not None and first >= settings['fail_from']:
            raise RuntimeError('mineru exploded with secret text')
        return [[{'type': 'text', 'text': f'page {n}', 'bbox': [100, 100, 900, 200], 'page_idx': 0}]
                for n in range(first, last + 1)]
    server = ThreadingHTTPServer(('127.0.0.1', 0), pdf_mineru_gpu.make_handler(key='k1', extract=extract, ready=ready))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ready.set()
    yield f'http://127.0.0.1:{server.server_address[1]}', calls, settings
    server.shutdown()


def gpu_extractor(fake, url, key='k1'):
    extractor = fake(api_key=key)
    extractor.gpu_url = url
    return extractor


def test_the_gpu_service_reads_every_group(tmp_path, fake, gpu_service):
    url, calls, _ = gpu_service
    extractor = gpu_extractor(fake, url)
    doc = read(tmp_path, extractor, 40)
    assert texts(doc) == [[f'page {n}'] for n in range(1, 41)]
    assert [(a, b) for a, b, _ in calls] == [(1, 20), (21, 40)]      # 40 pages: two even groups
    assert all(head == b'%PDF-' for _, _, head in calls)
    assert fake.runs() == []                      # no MinerU process on the CPU
    assert extractor.gpu_read == 40 and extractor.gpu_error is None


def test_after_a_gpu_failure_the_rest_is_read_on_the_cpu(tmp_path, fake, gpu_service):
    url, calls, settings = gpu_service
    settings['fail_from'] = 21
    extractor = gpu_extractor(fake, url)
    doc = read(tmp_path, extractor, 40)
    assert texts(doc) == [[f'page {n}'] for n in range(1, 41)]
    assert [(a, b) for a, b, _ in calls] == [(1, 20), (21, 40)]
    assert fake.runs() == [{'page': None, 'first': '21', 'last': '40', 'key': 'k1'}]
    assert extractor.gpu_read == 20 and extractor.gpu_error == 'HTTPError'


def test_a_wrong_key_or_no_service_falls_back_to_the_cpu(tmp_path, fake, gpu_service):
    url, calls, _ = gpu_service
    extractor = gpu_extractor(fake, url, key='wrong')
    doc = read(tmp_path, extractor, 3)
    assert texts(doc) == [['page 1'], ['page 2'], ['page 3']] and calls == []
    assert extractor.gpu_error == 'HTTPError'
    extractor = gpu_extractor(fake, 'http://127.0.0.1:9')
    (tmp_path / 'work').rename(tmp_path / 'work-1')
    read(tmp_path, extractor, 3)
    assert extractor.gpu_error == 'URLError'


def test_the_gpu_service_checks_its_requests(gpu_service):
    import urllib.error
    import urllib.request
    url, calls, settings = gpu_service

    def post(path, data=b'%PDF-1.4 x', key='k1'):
        request = urllib.request.Request(url + path, data=data, method='POST',
                                         headers={'Authorization': f'Bearer {key}'} if key else {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    assert post('/pages?first=1&last=2', key=None)[0] == 401
    assert post('/pages?first=1&last=2', key='k2')[0] == 401
    assert post('/pages?first=2&last=1')[0] == 400
    assert post('/pages?first=1&last=65')[0] == 400
    assert post('/pages?first=1&last=2', data=b'not a pdf')[0] == 400
    assert post('/pages?first=1')[0] == 400
    assert post('/pages?first=1&last=2') == (200, {'pages': [
        [{'type': 'text', 'text': 'page 1', 'bbox': [100, 100, 900, 200], 'page_idx': 0}],
        [{'type': 'text', 'text': 'page 2', 'bbox': [100, 100, 900, 200], 'page_idx': 0}]]})
    settings['fail_from'] = 1
    assert post('/pages?first=1&last=2') == (500, {'error': 'RuntimeError'})   # no message: it may hold document text
    with urllib.request.urlopen(url + '/health', timeout=5) as response:
        assert response.status == 200


def test_the_gpu_service_exits_when_idle_after_a_call():
    import threading
    from backend.worker.pdf_mineru_gpu import Activity, exit_when_idle
    activity, exits, now = Activity(), [], [0.0]

    def run(seconds):
        stop = threading.Event()
        thread = threading.Thread(target=exit_when_idle, args=(activity, 600),
                                  kwargs={'exit': exits.append, 'clock': lambda: now[0], 'every': 0.01, 'stop': stop})
        thread.start()
        time.sleep(seconds)
        stop.set()
        thread.join()
    now[0] = 10_000
    run(0.05)
    assert exits == []                      # no call yet: the models stay loaded
    activity.last, activity.busy = 9_000, True
    run(0.05)
    assert exits == []                      # a call is running
    activity.busy = False
    activity.last = 9_500
    run(0.05)
    assert exits == []                      # idle 500 s only
    activity.last = 9_000
    run(0.05)
    assert exits == [0]


@pytest.mark.parametrize('pages, sizes', [(81, [27, 27, 27]), (40, [20, 20]), (32, [32]), (33, [17, 16]),
                                          (1, [1]), (65, [22, 22, 21])])
def test_gpu_groups_are_even(pages, sizes):
    got, left = [], pages
    while left:
        size = pdf_mineru.even_group_size(left, pdf_mineru.GPU_PAGES_PER_CALL)
        got.append(size)
        left -= size
    assert got == sizes
