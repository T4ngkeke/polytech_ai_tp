"""Resumable, isolated M3-C PDF extraction. No application database is used.

Each page's frozen MinerU content_list is cached before adapting it to the
production PDF IR. Run with the same local model/config as the September test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from backend.worker.pdf_mineru import extract_page
from backend.worker.pdf_reader import CONFIG, READER_VERSION, read_pdf

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def run(question_map: Path, output: Path, server_url: str, config: Path) -> None:
    questions = json.loads(question_map.read_text())['questions']
    files = {e['pdf_path']: e['pdf_sha256'] for q in questions for e in q['required_evidence']}
    if not config.is_file():
        raise FileNotFoundError(config)
    if not os.getenv('MINERU_TOOLS_CONFIG_JSON'):
        os.environ['MINERU_TOOLS_CONFIG_JSON'] = str(config)
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    for relative, digest in sorted(files.items()):
        source = ROOT / relative
        if sha(source) != digest:
            raise ValueError(f'PDF changed: {relative}')
        stem = source.stem
        cache = output / 'content-list' / stem
        work = Path('/tmp/m3c-pdf-extract') / stem
        work.mkdir(parents=True, exist_ok=True)
        failures: list[dict] = []

        def cached_extract(path: Path, page_no: int, raw_dir: Path) -> list[dict]:
            destination = cache / f'page-{page_no}.json'
            if destination.exists():
                saved = json.loads(destination.read_text())
                if saved['pdf_sha256'] != digest or saved['page_no'] != page_no:
                    raise ValueError('stale_page_cache')
                return saved['content_list']
            start = time.monotonic()
            try:
                content = extract_page(path, page_no, raw_dir, server_url=server_url,
                                       config_path=str(config))
            except Exception as exc:
                failures.append({'page_no': page_no, 'error_type': type(exc).__name__,
                                 'elapsed_seconds': round(time.monotonic() - start, 3)})
                print('FAILED', stem, page_no, type(exc).__name__, flush=True)
                raise
            save(destination, {'pdf_sha256': digest, 'page_no': page_no,
                               'elapsed_seconds': round(time.monotonic() - start, 3),
                               'content_list': content})
            print('PAGE', stem, page_no, len(content), flush=True)
            return content

        started = time.monotonic()
        document, timing = read_pdf(source, digest, work, cached_extract)
        save(output / 'parsed' / f'{stem}.json', {
            'pdf_path': relative, 'pdf_sha256': digest,
            'reader_version': READER_VERSION, 'reader_config': CONFIG,
            'question_map_sha256': sha(question_map),
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'pages': [p.to_dict() for p in document.pages],
            'timing': timing, 'failures': failures,
        })
        print('DOCUMENT', stem, len(document.pages), len(failures), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--question-map', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--server-url', default='http://127.0.0.1:18003/v1')
    args = parser.parse_args()
    run(args.question_map, args.output, args.server_url, args.config)
