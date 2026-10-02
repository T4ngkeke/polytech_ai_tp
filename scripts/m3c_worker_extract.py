"""Resumable WorkerAdapter baseline over M3-C's four complete PDF courses."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import pymupdf

from scripts.docling_benchmark.adapters import WorkerAdapter

ROOT = Path(__file__).resolve().parents[1]


def save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


async def run(question_map: Path, output: Path, base_url: str, model: str) -> None:
    mapping = json.loads(question_map.read_text())
    files = {e['pdf_path']: e['pdf_sha256'] for q in mapping['questions'] for e in q['required_evidence']}
    adapter = WorkerAdapter(base_url, model)
    for relative, digest in sorted(files.items()):
        source = ROOT / relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError(f'PDF changed: {relative}')
        with pymupdf.open(source) as pdf:
            count = len(pdf)
        stem = source.stem
        for page_no in range(1, count + 1):
            destination = output / 'worker' / stem / f'page-{page_no}.json'
            if destination.exists():
                row = json.loads(destination.read_text())
                if row['file_sha256'] != digest or row['page_no'] != page_no:
                    raise ValueError('Stale worker cache')
                continue
            row, audit = await adapter.read(source, page_no, digest)
            result = row.dump()
            result['audit'] = audit
            save(destination, result)
            print('WORKER_PAGE', stem, page_no, result['status'],
                  result['calls'], round(result['seconds'], 2), flush=True)
        print('WORKER_DOCUMENT', stem, count, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--question-map', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:18001/v1')
    parser.add_argument('--model', default='ornith')
    args = parser.parse_args()
    asyncio.run(run(args.question_map, args.output, args.base_url, args.model))
