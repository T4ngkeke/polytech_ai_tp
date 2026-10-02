"""Offline M3-C page retrieval on the four complete frozen PDF courses.

Uses the product's split_block and reciprocal_rank_fusion functions, real
BGE-M3 embeddings, and PostgreSQL french tsvector/ts_rank. The database must
be an explicitly named disposable *_test database; no application tables are
read or modified. This is a development-set retrieval experiment, not a
student-facing search endpoint or independent human acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

import asyncpg
from openai import AsyncOpenAI

from backend.app.services.retrieval_service import reciprocal_rank_fusion
from backend.worker.structured_index import CHUNKER_VERSION, split_block

ROOT = Path(__file__).resolve().parents[1]
REVIEW_CODES = {'extraction_failure', 'source_identity_conflict', 'content_review'}
WORD_RE = re.compile(r'\w+', re.UNICODE)


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def corpus(question_map: Path, root: Path, variant: str) -> tuple[list[dict], list[dict]]:
    mapping = json.loads(question_map.read_text())
    files = {e['pdf_path']: e['pdf_sha256'] for q in mapping['questions'] for e in q['required_evidence']}
    chunks: list[dict] = []
    pages: list[dict] = []
    for path, digest in sorted(files.items()):
        stem = Path(path).stem
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f'PDF hash changed: {path}')
        if variant == 'mineru-text':
            document = json.loads((root / 'parsed' / f'{stem}.json').read_text())
            if document['pdf_sha256'] != digest:
                raise ValueError(f'Parsed PDF hash changed: {path}')
            source_pages = document['pages']
        else:
            source_pages = []
            for page_no in range(1, next(e['pdf_page_count'] for q in mapping['questions']
                                         for e in q['required_evidence'] if e['pdf_path'] == path) + 1):
                item = json.loads((root / 'worker' / stem / f'page-{page_no}.json').read_text())
                if item['file_sha256'] != digest or item['page_no'] != page_no:
                    raise ValueError(f'Worker page source changed: {path} p{page_no}')
                source_pages.append(item)
        for page_no, page in enumerate(source_pages, 1):
            actual_page = page['source_slide_no'] if variant == 'mineru-text' else page['page_no']
            if actual_page != page_no:
                raise ValueError(f'Physical page order mismatch: {path} p{page_no}')
            page_issues = page.get('issues', []) if variant == 'mineru-text' else page.get('errors', [])
            blocks = page['blocks'] if variant == 'mineru-text' else page['segments']
            block_issues = [issue.get('code') for block in blocks for issue in block.get('issues', [])]
            pages.append({'pdf_path': path, 'page_no': page_no,
                          'state': page.get('state') if variant == 'mineru-text' else page.get('status'),
                          'issues': page_issues, 'block_issue_codes': block_issues,
                          'reviews_count': len(page.get('reviews', []))})
            for block_no, block in enumerate(blocks):
                content = block.get('content') or ''
                if not content.strip():
                    continue
                if variant == 'mineru-text' and any(
                    issue.get('resolution', 'pending') == 'pending' and issue.get('code') in REVIEW_CODES
                    for issue in block.get('issues', [])
                ):
                    continue
                ref = block.get('source_ref') or {}
                if ref.get('file_sha256') != digest:
                    raise ValueError(f'Block source hash mismatch: {path} p{page_no}')
                ref_page = ref.get('source_slide_no') if variant == 'mineru-text' else ref.get('page_no')
                if ref_page != page_no:
                    raise ValueError(f'Block source page mismatch: {path} p{page_no}')
                for part_no, part in enumerate(split_block(content)):
                    chunks.append({'id': sha(f'{variant}\x1f{path}\x1f{page_no}\x1f{block_no}\x1f{part_no}'),
                                   'pdf_path': path, 'pdf_sha256': digest, 'page_no': page_no,
                                   'block_no': block_no, 'part_no': part_no,
                                   'content_start': part.start, 'content_end': part.end,
                                   'content': part.content, 'content_sha256': sha(part.content)})
    return chunks, pages


class EmbedCache:
    def __init__(self, path: Path, client: AsyncOpenAI, model: str):
        self.path, self.client, self.model = path, client, model
        self.vectors: dict[str, list[float]] = {}
        self.calls = self.prompt_tokens = self.new_texts = 0
        if path.exists():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row['model'] != model or len(row['embedding']) != 1024:
                    raise ValueError('Embedding cache model or dimension mismatch')
                self.vectors[row['text_sha256']] = row['embedding']

    async def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [sha(text) for text in texts]
        missing = {key: text for key, text in zip(keys, texts) if key not in self.vectors}
        if missing:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pairs = list(missing.items())
            with self.path.open('a') as handle:
                for pos in range(0, len(pairs), 16):
                    batch = pairs[pos:pos + 16]
                    response = await self.client.embeddings.create(
                        model=self.model, input=[text for _, text in batch])
                    if len(response.data) != len(batch):
                        raise ValueError('Embedding count mismatch')
                    for (key, _), item in zip(batch, sorted(response.data, key=lambda x: x.index)):
                        if len(item.embedding) != 1024:
                            raise ValueError('Embedding dimension mismatch')
                        self.vectors[key] = item.embedding
                        handle.write(json.dumps({'text_sha256': key, 'model': self.model,
                                                 'embedding': item.embedding}) + '\n')
                    handle.flush()
                    self.calls += 1
                    self.new_texts += len(batch)
                    self.prompt_tokens += response.usage.prompt_tokens if response.usage else 0
                    print('EMBED', self.new_texts, '/', len(pairs), flush=True)
        return [self.vectors[key] for key in keys]


def vector_literal(values: list[float]) -> str:
    return '[' + ','.join(format(value, '.8g') for value in values) + ']'


async def run(args) -> None:
    from urllib.parse import urlsplit
    pg_url = args.pg_url_file.read_text().strip()
    if not urlsplit(pg_url).path.removeprefix('/').endswith('_test'):
        raise ValueError('Only an explicit disposable *_test PostgreSQL database is allowed')
    chunks, pages = corpus(args.question_map, args.output, args.variant)
    mapping = json.loads(args.question_map.read_text())
    started = time.monotonic()
    async with AsyncOpenAI(base_url=args.embedding_url, api_key='local', timeout=180,
                           max_retries=0) as client:
        cache = EmbedCache(args.output / 'embedding-cache.jsonl', client, args.embedding_model)
        vectors = await cache.embed([item['content'] for item in chunks])
        questions = mapping['questions']
        qvectors = await cache.embed([item['question'] for item in questions])
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute('DROP TABLE IF EXISTS m3c_chunks')
        await conn.execute('''CREATE TABLE m3c_chunks (
            id text PRIMARY KEY, pdf_path text NOT NULL, page_no int NOT NULL,
            content text NOT NULL, embedding vector(1024) NOT NULL,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('french', content)) STORED)''')
        async with conn.transaction():
            await conn.executemany('''INSERT INTO m3c_chunks (id,pdf_path,page_no,content,embedding)
                VALUES ($1,$2,$3,$4,$5::vector)''',
                [(item['id'], item['pdf_path'], item['page_no'], item['content'], vector_literal(vector))
                 for item, vector in zip(chunks, vectors, strict=True)])
        by_id = {c['id']: c for c in chunks}
        results = []
        for question, embedding in zip(questions, qvectors, strict=True):
            vector_rows = await conn.fetch('''SELECT id FROM m3c_chunks
                ORDER BY embedding <=> $1::vector LIMIT 20''', vector_literal(embedding))
            words = WORD_RE.findall(question['question'])
            lexical_rows = await conn.fetch('''SELECT id FROM m3c_chunks
                WHERE tsv @@ to_tsquery('french', $1)
                ORDER BY ts_rank(tsv, to_tsquery('french', $1)) DESC LIMIT 20''',
                ' | '.join(words)) if words else []
            ranked = reciprocal_rank_fusion([[row['id'] for row in vector_rows],
                                            [row['id'] for row in lexical_rows]])
            ordered_chunks = [by_id[chunk_id] for chunk_id, _ in ranked]
            seen: set[tuple[str, int]] = set()
            ordered_pages = []
            for item in ordered_chunks:
                key = item['pdf_path'], item['page_no']
                if key not in seen:
                    seen.add(key)
                    ordered_pages.append({'pdf_path': key[0], 'page_no': key[1],
                                          'first_chunk_id': item['id'],
                                          'first_chunk_content': item['content'][:400]})
            gold = {(e['pdf_path'], e['pdf_page_no']) for e in question['required_evidence']}
            ranks = {key: i + 1 for i, item in enumerate(ordered_pages)
                     if (key := (item['pdf_path'], item['page_no'])) in gold}
            results.append({'question_id': question['question_id'], 'question': question['question'],
                            'mapped_evidence': [{'pdf_path': path, 'page_no': page} for path, page in sorted(gold)],
                            'evidence_ranks': [{'pdf_path': path, 'page_no': page, 'rank': ranks.get((path, page))}
                                               for path, page in sorted(gold)],
                            'top_pages': ordered_pages[:10],
                            'top_chunks': [{k: c[k] for k in ('id','pdf_path','page_no','block_no',
                                                               'part_no','content_start','content_end','content')}
                                           for c in ordered_chunks[:10]],
                            'vector_ids': [row['id'] for row in vector_rows],
                            'lexical_ids': [row['id'] for row in lexical_rows],
                            'any_hit': {str(k): any(rank <= k for rank in ranks.values()) for k in (1, 3, 5)},
                            'all_pages': {str(k): len(ranks) == len(gold) and all(rank <= k for rank in ranks.values())
                                          for k in (1, 3, 5)}})
        summary = {'variant': args.variant, 'status': 'development_set_model_retrieval',
                   'question_map_sha256': hashlib.sha256(args.question_map.read_bytes()).hexdigest(),
                   'chunker_version': CHUNKER_VERSION, 'embedding_model': args.embedding_model,
                   'ranking': 'vector_top20 + french_tsvector_OR_top20 + product_RRF_k60; no_reranker',
                   'documents': 4, 'pages': len(pages), 'chunks': len(chunks),
                   'page_issues': [p for p in pages if p['issues'] or p['block_issue_codes'] or p['reviews_count'] or p['state'] in ('partial','unavailable','failed','needs_review')],
                   'embedding_new_calls': cache.calls, 'embedding_new_texts': cache.new_texts,
                   'embedding_new_prompt_tokens': cache.prompt_tokens,
                   'embedding_cache_items': len(cache.vectors),
                   'elapsed_seconds': round(time.monotonic() - started, 3),
                   'any_hit': {str(k): sum(q['any_hit'][str(k)] for q in results) for k in (1, 3, 5)},
                   'all_pages': {str(k): sum(q['all_pages'][str(k)] for q in results) for k in (1, 3, 5)}}
        save(args.output / 'results' / f'{args.variant}.json', {'summary': summary, 'questions': results,
                                                               'corpus_pages': pages})
        print('RESULT', args.variant, summary['any_hit'], summary['all_pages'], flush=True)
    finally:
        await conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--question-map', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--variant', choices=['mineru-text', 'worker'], required=True)
    parser.add_argument('--pg-url-file', type=Path, required=True)
    parser.add_argument('--embedding-url', default='http://172.18.0.5:11434/v1')
    parser.add_argument('--embedding-model', default='bge-m3:latest')
    asyncio.run(run(parser.parse_args()))
