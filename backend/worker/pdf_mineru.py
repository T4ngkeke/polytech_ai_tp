"""Bounded MinerU hybrid page extraction; the only module importing MinerU.

MinerU runs in its own interpreter (pdf_mineru_cli), never in the worker's
process. One process reads a whole document, PAGES_PER_CALL pages per MinerU
call, so MinerU and its models load once per document. When a group of pages
fails, those pages are read again one process per page, so one bad page does
not cost the others. The returned content_list is data, never a database or
publication instruction.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path


FROZEN_MINERU_VERSION = '3.4.5'
# Pages in one MinerU call. MinerU keeps the page images of a call in memory:
# 16 pages stay well under 1.5 GB; 64 went over 3 GB on an 81-page PDF.
PAGES_PER_CALL = 16
# On the GPU service (pdf_mineru_gpu): at most this many pages per call, the
# pages spread evenly over the calls (81 pages: 27, 27, 27, no single last
# page). 81 pages read in 93 and 101 s with 32, 108 and 114 s with 16, 103 s
# with 64; 32 takes about 0.5 GB more GPU memory than 16.
GPU_PAGES_PER_CALL = 32
POLL_SECONDS = 0.2


class MinerUUnavailable(RuntimeError):
    pass


def extract_pages(path: Path, first: int, last: int, output_dir: Path, *,
                  server_url: str, config_path: str) -> list[list[dict]]:
    """Pages first..last (1-based) in one MinerU call: the blocks of each page,
    each with page_idx 0 as for a one-page call."""
    if not Path(config_path).is_absolute() or not Path(config_path).is_file():
        raise MinerUUnavailable('mineru_config_unavailable')
    if not server_url:
        raise MinerUUnavailable('mineru_server_unavailable')
    os.environ['MINERU_TOOLS_CONFIG_JSON'] = config_path
    os.environ['MINERU_MODEL_SOURCE'] = 'local'
    try:
        from mineru.cli.common import do_parse
    except ImportError as exc:
        raise MinerUUnavailable('mineru_not_installed') from exc
    output_dir.mkdir(parents=True, exist_ok=False)
    # image_analysis is off: MinerU would describe each picture, and nothing
    # uses that text (the picture captions come from our own model).
    do_parse(str(output_dir), ['page'], [path.read_bytes()], ['ch'],
             backend='hybrid-http-client', parse_method='auto', formula_enable=True,
             table_enable=True, server_url=server_url.removesuffix('/v1'), effort='high',
             image_analysis=False, f_draw_layout_bbox=False, f_draw_span_bbox=False,
             f_dump_md=True, f_dump_middle_json=False, f_dump_model_output=False,
             f_dump_orig_pdf=False, f_dump_content_list=True,
             start_page_id=first - 1, end_page_id=last - 1)
    matches = list(output_dir.glob('page/*/page_content_list.json'))
    if len(matches) != 1:
        raise RuntimeError('mineru_content_list_missing')
    value = json.loads(matches[0].read_text())
    if not isinstance(value, list):
        raise ValueError('mineru_content_list_invalid')
    pages: list[list[dict]] = [[] for _ in range(last - first + 1)]
    for item in value:
        index = item.get('page_idx') if isinstance(item, dict) else None
        if first == last and index is None:
            index = 0
        if not isinstance(index, int) or not 0 <= index < len(pages):
            raise ValueError('mineru_content_list_invalid')
        pages[index].append({**item, 'page_idx': 0})
    return pages


def extract_page(path: Path, page_no: int, output_dir: Path, *, server_url: str, config_path: str) -> list[dict]:
    return extract_pages(path, page_no, page_no, output_dir, server_url=server_url, config_path=config_path)[0]


def even_group_size(pages: int, most: int) -> int:
    """The size of the next group when `pages` pages are left: as few groups
    as `most` allows, of nearly equal size."""
    groups = -(-pages // most)
    return -(-pages // groups)


def _stop(process: subprocess.Popen) -> None:
    """Kill the MinerU process and the helper processes it started (its own
    process group), also when it has already exited."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait()


class DocumentExtractor:
    """extract(path, page_no, output_dir) for read_pdf, which asks for the pages
    in order. The first request starts one MinerU process that reads from that
    page to the end and writes page-<n>.json as each group of pages is done.
    A failed or stalled group (no page for timeout_seconds) stops the process;
    the pages of that group are read one process per page, and the next group
    starts a new process. close() stops a process still running.

    With gpu_url (the service of pdf_mineru_gpu: MinerU's small models on the
    GPU), each group is read there instead. When it fails once, the rest of the
    document is read here, on the CPU, as above; gpu_error says why."""

    def __init__(self, *, python_executable: str, server_url: str, config_path: str,
                 timeout_seconds: int, api_key: str = '', gpu_url: str = ''):
        self.base = [python_executable, '-m', 'backend.worker.pdf_mineru_cli',
                     '--server-url', server_url, '--config', config_path]
        self.env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        # MinerU's HTTP client sends this as its bearer key (mineru_vl_utils).
        if api_key:
            self.env['MINERU_VL_API_KEY'] = api_key
        self.timeout = timeout_seconds
        self.process: subprocess.Popen | None = None
        self.first = self.last = 0
        self.out: Path | None = None
        self.progress = 0.0
        self.alone: set[int] = set()
        self.api_key = api_key
        self.gpu_url = gpu_url.rstrip('/')
        self.gpu_pages: dict[int, list[dict]] = {}
        self.gpu_read = 0
        self.gpu_error: str | None = None
        self.pdf: tuple[Path, bytes, int] | None = None

    def __call__(self, path: Path, page_no: int, output_dir: Path) -> list[dict]:
        if self.gpu_url and self.gpu_error is None:
            try:
                return self._from_gpu(Path(path), page_no)
            except Exception as exc:  # noqa: BLE001 - the rest of the document is read on the CPU
                self.gpu_error = type(exc).__name__
                self.gpu_pages.clear()
        if page_no not in self.alone:
            try:
                return self._from_group(Path(path), page_no, Path(output_dir))
            except Exception:  # noqa: BLE001 - the pages of this group are read again one by one
                group_end = min(self.last, self.first + (page_no - self.first) // PAGES_PER_CALL * PAGES_PER_CALL
                                + PAGES_PER_CALL - 1)
                self.close()
                self.alone.update(range(page_no, group_end + 1))
        return self._alone(Path(path), page_no, Path(output_dir))

    def close(self) -> None:
        if self.process is not None:
            _stop(self.process)
            self.process = None

    def _from_gpu(self, path: Path, page_no: int) -> list[dict]:
        import urllib.request
        if page_no not in self.gpu_pages:
            if self.pdf is None or self.pdf[0] != path:
                import pymupdf
                with pymupdf.open(path) as pdf:
                    count = len(pdf)
                self.pdf = (path, path.read_bytes(), count)
            last = page_no + even_group_size(self.pdf[2] - page_no + 1, GPU_PAGES_PER_CALL) - 1
            request = urllib.request.Request(
                f'{self.gpu_url}/pages?first={page_no}&last={last}', data=self.pdf[1], method='POST',
                headers={'Content-Type': 'application/pdf', 'Authorization': f'Bearer {self.api_key}'})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                pages = json.loads(response.read())['pages']
            if not isinstance(pages, list) or len(pages) != last - page_no + 1 \
                    or not all(isinstance(blocks, list) for blocks in pages):
                raise ValueError('mineru_gpu_reply_invalid')
            self.gpu_pages = dict(enumerate(pages, start=page_no))
        self.gpu_read += 1
        return self.gpu_pages.pop(page_no)

    def _start(self, path: Path, page_no: int, output_dir: Path) -> None:
        import pymupdf
        self.close()
        with pymupdf.open(path) as pdf:
            self.first, self.last = page_no, len(pdf)
        self.out = output_dir.parent / f'mineru-from-{page_no}'
        self.out.mkdir(parents=True, exist_ok=False)
        self.process = subprocess.Popen(
            self.base + ['--pdf', str(path), '--first', str(self.first), '--last', str(self.last),
                         '--out', str(self.out)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env, start_new_session=True)
        self.progress = time.monotonic()

    def _from_group(self, path: Path, page_no: int, output_dir: Path) -> list[dict]:
        if self.process is None or not self.first <= page_no <= self.last:
            self._start(path, page_no, output_dir)
        result = self.out / f'page-{page_no}.json'
        while not result.exists():
            if self.process.poll() is not None and not result.exists():
                # Logs may contain the source document or a secret endpoint. Keep
                # the worker error stable and inspect the process separately.
                raise RuntimeError('mineru_page_failed')
            if time.monotonic() - self.progress > self.timeout:
                raise TimeoutError('mineru_page_timeout')
            time.sleep(POLL_SECONDS)
        self.progress = time.monotonic()
        value = json.loads(result.read_text())
        if not isinstance(value, list):
            raise ValueError('mineru_content_list_invalid')
        if page_no == self.last:
            self.close()
        return value

    def _alone(self, path: Path, page_no: int, output_dir: Path) -> list[dict]:
        output_dir.mkdir(parents=True, exist_ok=False)
        process = subprocess.Popen(self.base + ['--pdf', str(path), '--page', str(page_no), '--out', str(output_dir)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('mineru_page_timeout') from exc
        finally:
            _stop(process)
        if code:
            raise RuntimeError('mineru_page_failed')
        value = json.loads((output_dir / 'result.json').read_text())
        if not isinstance(value, list):
            raise ValueError('mineru_content_list_invalid')
        return value


def subprocess_extractor(*, python_executable: str, server_url: str, config_path: str,
                         timeout_seconds: int = 240, api_key: str = '', gpu_url: str = '') -> DocumentExtractor:
    """Use a separate interpreter so MinerU does not load into the main worker."""
    if not Path(python_executable).is_file():
        raise MinerUUnavailable('mineru_python_unavailable')
    if not Path(config_path).is_absolute() or not Path(config_path).is_file():
        raise MinerUUnavailable('mineru_config_unavailable')
    return DocumentExtractor(python_executable=python_executable, server_url=server_url,
                             config_path=config_path, timeout_seconds=timeout_seconds, api_key=api_key,
                             gpu_url=gpu_url)
