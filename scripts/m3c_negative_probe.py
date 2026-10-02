"""Run frozen no-answer probes against both complete M3-C corpora."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

import asyncpg
from openai import AsyncOpenAI

from backend.app.services.retrieval_service import reciprocal_rank_fusion
from scripts.m3c_pdf_retrieve import EmbedCache, corpus, vector_literal, save


async def run(args) -> None:
    questions = json.loads(args.probes.read_text())['questions']
    url = args.pg_url_file.read_text().strip()
    if not url.split('/')[-1].endswith('_test'):
        raise ValueError('Disposable *_test database required')
    async with AsyncOpenAI(base_url=args.embedding_url, api_key='local', timeout=180,
                           max_retries=0) as client:
        cache = EmbedCache(args.output / 'embedding-cache.jsonl', client, 'bge-m3:latest')
        qvectors = await cache.embed([q['question'] for q in questions])
        result = {'probe_input_sha256': __import__('hashlib').sha256(args.probes.read_bytes()).hexdigest(),
                  'ranking': 'vector_top20 + french_tsvector_OR_top20 + product_RRF_k60; no relevance gate',
                  'variants': {}}
        db = await asyncpg.connect(url)
        try:
            for variant in ('mineru-text', 'worker'):
                chunks, pages = corpus(args.question_map, args.output, variant)
                vectors = await cache.embed([c['content'] for c in chunks])
                await db.execute('''CREATE TEMP TABLE probe_chunks (
                    id text PRIMARY KEY, pdf_path text, page_no int, content text,
                    embedding vector(1024),
                    tsv tsvector GENERATED ALWAYS AS (to_tsvector('french', content)) STORED)''')
                await db.executemany('''INSERT INTO probe_chunks (id,pdf_path,page_no,content,embedding)
                    VALUES ($1,$2,$3,$4,$5::vector)''',
                    [(c['id'],c['pdf_path'],c['page_no'],c['content'],vector_literal(v))
                     for c,v in zip(chunks,vectors,strict=True)])
                by_id = {c['id']:c for c in chunks}
                rows=[]
                for question,vector in zip(questions,qvectors,strict=True):
                    v=await db.fetch('SELECT id FROM probe_chunks ORDER BY embedding <=> $1::vector LIMIT 20',
                                     vector_literal(vector))
                    words=re.findall(r'\w+',question['question'],re.UNICODE)
                    t=await db.fetch('''SELECT id FROM probe_chunks
                        WHERE tsv @@ to_tsquery('french',$1)
                        ORDER BY ts_rank(tsv,to_tsquery('french',$1)) DESC LIMIT 20''',
                        ' | '.join(words))
                    fused=reciprocal_rank_fusion([[r['id'] for r in v],[r['id'] for r in t]])
                    seen=set();top=[]
                    for chunk_id,score in fused:
                        c=by_id[chunk_id];key=(c['pdf_path'],c['page_no'])
                        if key in seen:continue
                        seen.add(key)
                        top.append({'pdf_path':key[0],'page_no':key[1],
                                    'first_chunk_content':c['content'][:400],
                                    'rrf_chunk_score':round(score,6)})
                        if len(top)==5:break
                    rows.append({'question_id':question['id'],'question':question['question'],
                                 'returned_pages':top,'no_relevance_gate':True})
                result['variants'][variant]={'pages':len(pages),'chunks':len(chunks),'questions':rows}
                await db.execute('DROP TABLE probe_chunks')
        finally:
            await db.close()
    save(args.output / 'results' / 'negative-probes.json',result)
    print('NEGATIVE_PROBES', {k:len(v['questions']) for k,v in result['variants'].items()})


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--question-map',type=Path,required=True)
    parser.add_argument('--probes',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pg-url-file',type=Path,required=True)
    parser.add_argument('--embedding-url',default='http://127.0.0.1:11435/v1')
    asyncio.run(run(parser.parse_args()))
