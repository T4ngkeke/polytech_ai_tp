"""Isolated MinerU process entrypoint. JSON stays in this job's private task dir."""
from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path

from backend.worker.pdf_mineru import FROZEN_MINERU_VERSION, extract_page


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--page',type=int,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--server-url',required=True)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    if version('mineru') != FROZEN_MINERU_VERSION:
        raise RuntimeError('mineru_version_mismatch')
    blocks=extract_page(args.pdf,args.page,args.out / 'mineru',
                        server_url=args.server_url,config_path=args.config)
    (args.out / 'result.json').write_text(json.dumps(blocks,ensure_ascii=False))


if __name__=='__main__': main()
