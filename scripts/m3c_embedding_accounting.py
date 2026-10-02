"""Repeat equal BGE-M3 input workloads for comparable service-side usage.

This intentionally re-embeds each variant independently. The measured tokens
and calls are a standardized repeat, not the total cost of earlier resumable
index runs (which reused vectors across variants and changed CPU to GPU).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from openai import AsyncOpenAI

from scripts.m3c_pdf_retrieve import corpus, save


async def run(args) -> None:
    results = {}
    async with AsyncOpenAI(base_url=args.embedding_url, api_key='local', timeout=180,
                           max_retries=0) as client:
        for variant in ('mineru-text', 'worker'):
            chunks, _ = corpus(args.question_map, args.output, variant)
            texts = [item['content'] for item in chunks]
            batches = []
            started = time.monotonic()
            for pos in range(0, len(texts), 64):
                batch_texts = texts[pos:pos + 64]
                response = await client.embeddings.create(model='bge-m3:latest', input=batch_texts)
                if len(response.data) != len(batch_texts) or any(len(x.embedding) != 1024 for x in response.data):
                    raise ValueError('Invalid embedding response')
                batches.append({'inputs': len(batch_texts),
                                'prompt_tokens': response.usage.prompt_tokens if response.usage else None})
                print('USAGE', variant, pos + len(batch_texts), '/', len(texts), flush=True)
            results[variant] = {'chunk_count': len(chunks), 'embedding_inputs': len(texts),
                                'calls': len(batches),
                                'prompt_tokens': sum(x['prompt_tokens'] for x in batches)
                                                 if all(x['prompt_tokens'] is not None for x in batches) else None,
                                'elapsed_seconds': round(time.monotonic() - started, 3),
                                'batches': batches}
    save(args.output / 'results' / 'embedding-accounting.json',
         {'scope': 'independent repeat on the same isolated GPU BGE-M3 service; every product chunk (including duplicate text), batch size 64, no query embeddings',
          'model': 'bge-m3:latest', 'batch_size': 64, 'variants': results})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--question-map', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--embedding-url', default='http://127.0.0.1:11435/v1')
    asyncio.run(run(parser.parse_args()))
