"""MinerU's small models on the GPU, loaded once: the container edullm-mineru-gpu
(mineru_gpu/Dockerfile, documentation/PDF_INGESTION.md).

MinerU reads a page with its VLM (the server edullm-mineru-vlm) and three small
models: layout (it finds the formulas inside sentences), formula recognition
and OCR line detection. On the worker's CPU they take most of the reading
time; here they run on the GPU, about three times faster for a whole course.

This machine's CPU and GPU share one memory, and CUDA can use only the memory
that is really free (not the file cache). The models are loaded at start and
stay (about 2 GB). The working memory of a call (up to about 2 GB more) is
given back after each call, as MinerU does: kept, it held 6 GB in all. Some
of it stays all the same (1.5 GB after an 81-page course), so the service
exits after IDLE_EXIT_SECONDS without a call, and Docker starts it again
(--restart unless-stopped, about 40 s to load). When a call finds too little
free memory, or the service is starting, the worker reads on its CPU.

POST /pages?first=N&last=M, the PDF as the body, Authorization: Bearer <key>:
200 with the blocks of each page (pdf_mineru.extract_pages). One call at a
time. GET /health: 200 when the models are loaded.
"""
from __future__ import annotations

import hmac
import json
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_PDF_BYTES = 210 * 1024 * 1024     # the upload limit is 200 MB
MAX_PAGES_PER_CALL = 64
IDLE_EXIT_SECONDS = 600


class Activity:
    """When the last call ended (None: no call yet) and whether one runs."""
    def __init__(self):
        self.last: float | None = None
        self.busy = False


def exit_when_idle(activity: Activity, idle_seconds: float, *, exit=os._exit, clock=time.monotonic,
                   every: float = 30, stop: threading.Event | None = None) -> None:
    """Exit once a call has run and none came for idle_seconds."""
    stop = stop or threading.Event()
    while not stop.wait(every):
        if activity.last is not None and not activity.busy and clock() - activity.last > idle_seconds:
            exit(0)
            return


def prepare_gpu() -> None:
    """Load the small models on the GPU."""
    import torch
    # With little free memory, cuDNN finds no convolution algorithm on this GPU
    # ("no valid convolution algorithms available"); the plain kernels work.
    torch.backends.cudnn.enabled = False
    from mineru.backend.pipeline.model_init import HybridModelSingleton
    HybridModelSingleton().get_model(lang=None, formula_enable=True)


def warm_up(extract) -> None:
    """Read a small two-page PDF once, so that the first call of a teacher's
    document does not pay the GPU's first-run cost (about 10 s). A failure
    changes nothing: the models are loaded."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    work = Path(tempfile.mkdtemp(prefix='mineru-gpu-warm-'))
    try:
        pdf = work / 'warm.pdf'
        page = canvas.Canvas(str(pdf), pagesize=A4)
        for n in (1, 2):
            page.setFont('Helvetica-Bold', 20)
            page.drawString(72, 760, f'Warm-up page {n}')
            page.setFont('Helvetica', 12)
            page.drawString(72, 720, 'A sentence with a formula x2 + y2 = z2 and a second line of text.')
            page.drawString(72, 700, 'The convolution of f and g sums f(k) g(n - k) over k.')
            page.showPage()
        page.save()
        extract(pdf, 1, 2, work / 'out')
        print('mineru-gpu: warm-up done', flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f'mineru-gpu: warm-up failed ({type(exc).__name__})', flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        give_back_memory()


def give_back_memory() -> None:
    """Return the freed memory of a call (page images) to the system: without
    this the process kept about 0.7 GB more after each document."""
    import ctypes
    import gc
    gc.collect()
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass


def make_handler(*, key: str, extract, ready: threading.Event, activity: Activity | None = None):
    """extract(pdf_path, first, last, output_dir) -> list of page block lists."""
    lock = threading.Lock()
    activity = activity or Activity()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # no request lines: the paths are harmless, but keep the log quiet
            pass

        def reply(self, code: int, value) -> None:
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            given = self.headers.get('Authorization', '')
            return bool(key) and hmac.compare_digest(given.encode(), f'Bearer {key}'.encode())

        def do_GET(self):
            if urlparse(self.path).path != '/health':
                return self.reply(404, {'error': 'not_found'})
            return self.reply(200 if ready.is_set() else 503, {'ready': ready.is_set()})

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != '/pages':
                return self.reply(404, {'error': 'not_found'})
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if not ready.is_set():
                return self.reply(503, {'error': 'not_ready'})
            try:
                query = parse_qs(url.query)
                first, last = int(query['first'][0]), int(query['last'][0])
                size = int(self.headers.get('Content-Length', '0'))
            except (KeyError, ValueError):
                return self.reply(400, {'error': 'bad_request'})
            if not 1 <= first <= last or last - first >= MAX_PAGES_PER_CALL or not 0 < size <= MAX_PDF_BYTES:
                return self.reply(400, {'error': 'bad_request'})
            data = self.rfile.read(size)
            if len(data) != size or not data.startswith(b'%PDF-'):
                return self.reply(400, {'error': 'bad_request'})
            work = Path(tempfile.mkdtemp(prefix='mineru-gpu-'))
            try:
                pdf = work / 'source.pdf'
                pdf.write_bytes(data)
                with lock:
                    activity.busy = True
                    try:
                        pages = extract(pdf, first, last, work / 'out')
                    finally:
                        activity.busy, activity.last = False, time.monotonic()
            except Exception as exc:  # noqa: BLE001 - the worker reads these pages on the CPU
                # No message: it may contain document text or an endpoint.
                return self.reply(500, {'error': type(exc).__name__})
            finally:
                shutil.rmtree(work, ignore_errors=True)
                give_back_memory()
            return self.reply(200, {'pages': pages})

    return Handler


def main() -> None:
    from backend.worker.pdf_mineru import extract_pages
    key = os.environ.get('EDULLM_MODEL_API_KEY', '')
    if not key:
        raise SystemExit('EDULLM_MODEL_API_KEY is not set')
    os.environ.setdefault('MINERU_VL_API_KEY', key)       # MinerU's client sends it to the VLM server
    server_url, config = os.environ['PDF_MINERU_SERVER_URL'], os.environ['PDF_MINERU_CONFIG']
    os.environ['MINERU_TOOLS_CONFIG_JSON'] = config       # where the small models are, before they load
    ready, activity = threading.Event(), Activity()

    def extract(pdf, first, last, out):
        return extract_pages(pdf, first, last, out, server_url=server_url, config_path=config)
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8000'))),
                                 make_handler(key=key, extract=extract, ready=ready, activity=activity))
    prepare_gpu()
    warm_up(extract)        # not a call: it does not start the idle clock
    ready.set()
    idle = int(os.environ.get('IDLE_EXIT_SECONDS') or IDLE_EXIT_SECONDS)
    threading.Thread(target=exit_when_idle, args=(activity, idle), daemon=True).start()
    print('mineru-gpu: models loaded', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
