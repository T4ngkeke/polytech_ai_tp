"""Retrieval-only comparison of embedding and reranker models (no answer model).

The unit is a course facet of benchmarks v2, v3 and v4 (190 facets, 72
questions), all on corpus v3. A facet is found at k when one of its acceptable
pages is among the pages of the first k chunks that the raw question
retrieves, as the product's search does it: vector top 20 and BM25 top 20,
fused by RRF, then reranked.

1. export (in a disposable PostgreSQL database, via
   backend/tests/test_retrieval_eval_pg.py): builds corpus v3 with its frozen
   BGE-M3 vectors and writes, to OUT, the chunks, the questions with their
   facets, the BM25 top 20 of each question and the BGE-M3 query vectors.
2. scripts/retrieval_eval_models.py, in a GPU container: vectors of the chunks
   and questions for another embedding model. Then `candidates OUT` lists the
   fused candidates of every embedding model, `rerank-service OUT` scores them
   with the running product reranker, and retrieval_eval_models.py with others.
3. score (here): `python -m scripts.retrieval_eval score OUT` prints the
   table and writes OUT/results.json.

Vector ranking is done here in memory (cosine, as pgvector); export checks it
gives the same top 20 as the database for BGE-M3.
"""
from __future__ import annotations

import argparse
import json
import math
import operator
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPT = ROOT / 'documentation/ingestion-agent-acceptance/m4-agent'
BENCHMARKS = {'v2': ACCEPT / '2026-10-05-benchmark-v2', 'v3': ACCEPT / '2026-10-05-benchmark-v3',
              'v4': ACCEPT / '2026-10-06-benchmark-v4'}
RECALL_K = 20                      # each leg, as hybrid_search
CUTS = (1, 3, 6, 10)               # 6: research default top_k; 10: Search mode


def load_questions() -> list[dict]:
    rows = []
    for name, folder in BENCHMARKS.items():
        spec = json.loads((folder / 'questions.json').read_text())
        names = {code: Path(doc['pdf']).name for code, doc in spec['documents'].items()}
        for q in spec['questions']:
            facets = [{'id': f['facet_id'], 'pages': sorted({(names[a['doc']], a['page']) for a in f['acceptable']})}
                      for f in q.get('facets', []) if f.get('kind') == 'course' and f.get('acceptable')]
            if facets:
                rows.append({'id': f"{name}:{q['question_id']}", 'benchmark': name,
                             'question': q['question'], 'facets': facets})
    return rows


async def export(db, out: Path, embed_query) -> dict:
    """Write chunks.jsonl, questions.jsonl and vectors-bge-m3.json to OUT."""
    from sqlalchemy import select
    from backend.app.models import DocChunk, Document
    from backend.app.services.retrieval_service import _augmented_text, hybrid_search, vector_search

    out.mkdir(parents=True, exist_ok=True)
    docs = (await db.scalars(select(Document).where(Document.current_revision_id.is_not(None)))).all()
    snapshot = {d.id: d.current_revision_id for d in docs}
    names = {d.id: d.filename for d in docs}
    lab_id, class_id = docs[0].lab_id, docs[0].class_id
    chunks = (await db.scalars(select(DocChunk).where(DocChunk.revision_id.in_(snapshot.values()))
                               .order_by(DocChunk.document_id, DocChunk.chunk_index))).all()
    with (out / 'chunks.jsonl').open('w') as stream:
        for c in chunks:
            stream.write(json.dumps({'id': str(c.id), 'file': names[c.document_id], 'page': c.page_no,
                                     'text': c.content, 'rerank_text': _augmented_text(c)},
                                    ensure_ascii=False) + '\n')
    vectors = {'chunks': {str(c.id): list(map(float, c.embedding)) for c in chunks}, 'queries': {}}
    questions = load_questions()
    db_top = {}
    with (out / 'questions.jsonl').open('w') as stream:
        for q in questions:
            # No embedding and no reranker: hybrid_search returns the BM25 leg alone.
            bm25, _ = await hybrid_search(db, q['question'], None, lab_id, class_id=class_id,
                                          snapshot_revisions=snapshot, top_k=RECALL_K,
                                          recall_k=RECALL_K, language='auto')
            vector = await embed_query(q['question'])
            vectors['queries'][q['id']] = vector
            hits = await vector_search(db, vector, lab_id, k=RECALL_K, class_id=class_id,
                                       snapshot_revisions=snapshot)
            db_top[q['id']] = [str(h.id) for h in hits]
            stream.write(json.dumps({**q, 'bm25': [str(h.id) for h in bm25]}, ensure_ascii=False) + '\n')
    (out / 'vectors-bge-m3.json').write_text(json.dumps(vectors))
    (out / 'db-vector-top.json').write_text(json.dumps(db_top))
    return {'chunks': len(chunks), 'questions': len(questions),
            'facets': sum(len(q['facets']) for q in questions), 'documents': len(docs)}


def _unit(vector):
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


def vector_rank(query, matrix, ids):
    """Cosine top 20, as pgvector (plain Python: the backend has no numpy)."""
    q = _unit(query)
    scores = [sum(map(operator.mul, row, q)) for row in matrix]
    return [ids[i] for i in sorted(range(len(ids)), key=lambda i: -scores[i])[:RECALL_K]]


def load_vectors(path: Path):
    data = json.loads(path.read_text())
    ids = list(data['chunks'])
    return ids, [_unit(data['chunks'][i]) for i in ids], data['queries']


def candidates(out: Path) -> dict[str, list[str]]:
    """Fused candidates of every question for every embedding model: what a reranker must score."""
    from backend.app.services.retrieval_service import reciprocal_rank_fusion
    questions = [json.loads(line) for line in (out / 'questions.jsonl').read_text().splitlines()]
    union = {q['id']: [] for q in questions}
    for path in sorted(out.glob('vectors-*.json')):
        ids, matrix, queries = load_vectors(path)
        for q in questions:
            fused = [i for i, _ in reciprocal_rank_fusion([vector_rank(queries[q['id']], matrix, ids), q['bm25']])]
            union[q['id']] += [i for i in fused if i not in union[q['id']]]
    return union


def rerank_service(out: Path, url: str, model: str, name: str) -> None:
    """Scores of a running /rerank service for every candidate, through the
    product's rerank client (backend/app/services/model_clients.py), with
    its timing (one call per question, as Search makes)."""
    import asyncio
    import time
    from backend.app.services.model_clients import make_rerank_fn
    rerank = make_rerank_fn({'rerank_url': url, 'rerank_model': model})
    chunks = {c['id']: c for c in map(json.loads, (out / 'chunks.jsonl').read_text().splitlines())}
    questions = {q['id']: q for q in map(json.loads, (out / 'questions.jsonl').read_text().splitlines())}
    candidates = json.loads((out / 'candidates.json').read_text())

    async def run():
        result, seconds = {}, []
        for qid, ids in candidates.items():
            started = time.monotonic()
            scores = await rerank(questions[qid]['question'], [chunks[i]['rerank_text'] for i in ids])
            seconds.append((time.monotonic() - started, len(ids)))
            result[qid] = dict(zip(ids, scores))
        return result, seconds

    result, seconds = asyncio.run(run())
    (out / f'rerank-{name}.json').write_text(json.dumps(result))
    times = sorted(t for t, _ in seconds)
    print(f'{name}: {len(times)} calls, {sum(n for _, n in seconds)} passages, median {times[len(times) // 2]:.2f} s, '
          f'slowest {times[-1]:.2f} s per call')


def score(out: Path) -> dict:
    from backend.app.services.retrieval_service import reciprocal_rank_fusion
    chunks = {c['id']: c for c in map(json.loads, (out / 'chunks.jsonl').read_text().splitlines())}
    questions = [json.loads(line) for line in (out / 'questions.jsonl').read_text().splitlines()]
    rerankers = {p.stem.removeprefix('rerank-'): json.loads(p.read_text()) for p in sorted(out.glob('rerank-*.json'))}
    results = {}
    for path in sorted(out.glob('vectors-*.json')):
        embedding = path.stem.removeprefix('vectors-')
        ids, matrix, queries = load_vectors(path)
        if embedding == 'bge-m3':
            # The in-memory ranking must be the database's (pgvector cosine).
            db_top = json.loads((out / 'db-vector-top.json').read_text())
            # Same chunks; neighbours may swap places (float32 in pgvector).
            shared = [len(set(vector_rank(queries[q], matrix, ids)) & set(db_top[q])) for q in db_top]
            print(f'bge-m3: in-memory top 20 shares {sum(shared) / len(shared):.1f} chunks with the '
                  f'database on average (lowest {min(shared)})')
        for reranker in [None, *rerankers]:
            found = {k: 0 for k in (*CUTS, 'vector20', 'fused')}
            total = 0
            for q in questions:
                vec = vector_rank(queries[q['id']], matrix, ids)
                fused = [i for i, _ in reciprocal_rank_fusion([vec, q['bm25']])]
                if reranker:
                    scores = rerankers[reranker][q['id']]
                    fused = sorted(fused, key=lambda i: scores[i], reverse=True)
                pages = lambda ranked: {(chunks[i]['file'], chunks[i]['page']) for i in ranked}
                for facet in q['facets']:
                    gold = {tuple(p) for p in facet['pages']}
                    total += 1
                    found['vector20'] += bool(gold & pages(vec))
                    found['fused'] += bool(gold & pages(fused))
                    for k in CUTS:
                        found[k] += bool(gold & pages(fused[:k]))
            results[f'{embedding} + {reranker or "no reranker"}'] = {
                str(k): round(100 * v / total, 1) for k, v in found.items()} | {'facets': total}
    (out / 'results.json').write_text(json.dumps(results, indent=1))
    print(f"{'configuration':45} " + ' '.join(f'{c:>8}' for c in ('top1', 'top3', 'top6', 'top10', 'vec20', 'fused')))
    for name, row in results.items():
        print(f'{name:45} ' + ' '.join(f"{row[k]:>7}%" for k in ('1', '3', '6', '10', 'vector20', 'fused')))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('candidates').add_argument('out', type=Path)
    sub.add_parser('score').add_argument('out', type=Path)
    service = sub.add_parser('rerank-service')
    service.add_argument('out', type=Path)
    service.add_argument('--url', default='http://127.0.0.1:18004/rerank')
    service.add_argument('--model', default='BAAI/bge-reranker-v2-m3')
    service.add_argument('--name', default='bge-reranker-v2-m3')
    args = parser.parse_args()
    if args.command == 'candidates':
        (args.out / 'candidates.json').write_text(json.dumps(candidates(args.out)))
    elif args.command == 'rerank-service':
        rerank_service(args.out, args.url, args.model, args.name)
    else:
        score(args.out)


if __name__ == '__main__':
    sys.exit(main())
