"""Isolated MinerU process entrypoint. JSON stays in this job's private task dir.

--page N: one page, written to <out>/result.json.
--first N --last M: pages N..M, PAGES_PER_CALL pages per MinerU call; each page
is written to <out>/page-<n>.json as soon as its group is done.
"""
from __future__ import annotations

import argparse
import json
import os
from importlib.metadata import version
from pathlib import Path

from backend.worker.pdf_mineru import FROZEN_MINERU_VERSION, PAGES_PER_CALL, extract_page, extract_pages


def write(path: Path, value) -> None:
    """Write then rename, so the worker never reads half a file."""
    part = path.with_suffix('.part')
    part.write_text(json.dumps(value, ensure_ascii=False))
    os.replace(part, path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--page',type=int)
    parser.add_argument('--first',type=int)
    parser.add_argument('--last',type=int)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--server-url',required=True)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    if version('mineru') != FROZEN_MINERU_VERSION:
        raise RuntimeError('mineru_version_mismatch')
    if args.page is not None:
        blocks=extract_page(args.pdf,args.page,args.out / 'mineru',
                            server_url=args.server_url,config_path=args.config)
        write(args.out / 'result.json', blocks)
        return
    if args.first is None or args.last is None or not 1 <= args.first <= args.last:
        raise SystemExit('give --page, or --first and --last')
    for first in range(args.first, args.last + 1, PAGES_PER_CALL):
        last = min(args.last, first + PAGES_PER_CALL - 1)
        pages = extract_pages(args.pdf, first, last, args.out / f'mineru-{first}',
                              server_url=args.server_url, config_path=args.config)
        for page_no, blocks in enumerate(pages, start=first):
            write(args.out / f'page-{page_no}.json', blocks)


if __name__=='__main__': main()
