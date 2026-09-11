"""Read-only audit of already indexed cours PDFs; run inside the backend container.

Example: docker compose exec -T backend python - --lab-id UUID \
    < scripts/check_course_worker_quality.py > /tmp/course-quality.json
No uploads, configuration changes, model-generated grading, or database writes.
The retrieval questions are fixed, small checks for these two specific PDFs.
"""
import argparse
from contextlib import redirect_stdout
import sys
import asyncio
from collections import Counter
from dataclasses import asdict
import json
import re
import uuid

from sqlalchemy import select
from backend.app.database import AsyncSessionLocal, engine
from backend.app.models import Document, DocChunk
from backend.app.services.retrieval_service import hybrid_search
from backend.worker.main import _load_config, _make_real_embed_fn
from backend.worker.ingest import _default_parse
from backend.worker.chunking import chunk_pages
from backend.worker.parsing import extract_pdf_text
from openai import AsyncOpenAI

# Physical PDF page numbers, checked against source text before retrieval.
CASES = [
    ('semester', 'Quelle est la durée totale du semestre et combien y a-t-il de CM, TD et TP ?', 'main-2.pdf', [5, 6]),
    ('sizeof', 'Comment mesurer la taille en octets de int et double avec sizeof ?', 'main-2.pdf', [21, 22]),
    ('signed_overflow', 'Que dit le cours sur le débordement signé et non signé en C ?', 'main-2.pdf', [39, 50]),
    ('ieee754', 'Combien de bits pour le signe, exposant et mantisse du float IEEE 754 ?', 'main-2.pdf', [55]),
    ('gcc_error', 'Pourquoi gcc signale une erreur avant printf alors que le point-virgule manque à la ligne précédente ?', 'main-2.pdf', [64, 65]),
    ('calloc', 'Quelle différence entre malloc et calloc pour le contenu initial de la mémoire ?', 'main-3.pdf', [21, 22, 29, 30, 70]),
    ('realloc', 'Pourquoi utiliser un pointeur temporaire avec realloc quand il échoue ?', 'main-3.pdf', [46, 47]),
    ('growth', 'Pourquoi doubler la capacité du tableau plutôt que grandir une case à la fois ?', 'main-3.pdf', [49, 50]),
    ('ownership', 'Qui doit libérer la mémoire renvoyée par creer_tableau ?', 'main-3.pdf', [59, 60, 61]),
    ('full_example', 'Montrer la correction complète pour agrandir le tableau puis ajouter valeur et augmenter taille.', 'main-3.pdf', [54]),
]

async def main(lab_id):
    report = {'lab_id': str(lab_id), 'documents': [], 'retrieval': []}
    async with AsyncSessionLocal() as db:
        docs = (await db.execute(select(Document).where(Document.lab_id == lab_id).order_by(Document.filename))).scalars().all()
        assert {d.filename for d in docs} == {'main-2.pdf', 'main-3.pdf'}, 'Expected the two cours PDFs in an isolated lab'
        names = {d.id: d.filename for d in docs}
        for doc in docs:
            assert doc.status.value == 'indexed', f'{doc.filename}: {doc.status.value}'
            with redirect_stdout(sys.stderr):
                pages, gate = _default_parse(doc.storage_path)
                raw = extract_pdf_text(doc.storage_path)
            expected = chunk_pages(pages, markdown=False)
            rows = (await db.execute(select(DocChunk).where(DocChunk.document_id == doc.id).order_by(DocChunk.chunk_index))).scalars().all()
            counts = Counter(c.content for c in rows)
            norm = lambda s: re.sub(r'\s+', '', s)
            mismatch = [i+1 for i, page in enumerate(pages) if norm(page) != norm(''.join(c.content for c in rows if c.page_no == i+1))]
            report['documents'].append({
                'filename':doc.filename, 'id':str(doc.id), 'status':doc.status.value,
                'pages':len(pages), 'chunks':len(rows), 'gate':asdict(gate),
                'same_as_current_chunker':[(c.content,c.page_no,c.section) for c in rows] == [(c.content,c.page_no,c.section) for c in expected],
                'nonwhitespace_mismatch_pages':mismatch,
                'margin_removed_chars':sum(map(len,raw))-sum(map(len,pages)),
                'duplicate_extras':sum(n-1 for n in counts.values()),
                'shorter_than_80':sum(len(c.content)<80 for c in rows),
                'no_section':sum(c.section is None for c in rows),
                'no_context':sum(not c.context for c in rows),
                'reasoning_markers':sum(bool(re.search(r'</?(think|analysis|reasoning)|The user wants',c.context or '',re.I)) for c in rows),
                'overlong_context':sum(len(c.context or '')>400 for c in rows),
                'valid_vectors':sum(len(c.embedding)==1024 for c in rows),
                'fulltext_present':sum(c.tsv is not None for c in rows),
                'fingerprints':sum(bool(c.source_fingerprint) for c in rows),
                'ingest_report':doc.ingest_report,
                'repeated_examples':[{'count':n,'content':s} for s,n in counts.most_common(5)],
                'chunks_data':[{'index':c.chunk_index,'page':c.page_no,'section':c.section,'content':c.content,'context':c.context} for c in rows],
            })
        cfg = await _load_config(db)
        report['models']={'context':cfg['model'],'embedding':cfg['embedding_model'],'vlm_enabled':bool(cfg['vlm_model']),'reranker_used':False}
        async with AsyncOpenAI(api_key=cfg['embedding_api_key'],base_url=cfg['embedding_base_url'],timeout=180) as client:
            embed = _make_real_embed_fn(client,cfg['embedding_model'])
            vectors = await embed([c[1] for c in CASES])
        for (name,query,expected_file,expected_pages), vector in zip(CASES,vectors,strict=True):
            hits, filtered = await hybrid_search(db,query,vector,lab_id,class_id=docs[0].class_id,language='fr',top_k=5)
            matched = [i+1 for i,h in enumerate(hits) if names[h.document_id]==expected_file and h.page_no in expected_pages]
            if name == 'full_example':
                source = '\n'.join(h.content for h in hits if names[h.document_id] == expected_file and h.page_no == 54)
                compact = re.sub(r'\s+', '', source)
                report['full_example_parts'] = {
                    'resize_block_found': 'if(taille==capacite)' in compact and 'realloc(arr,' in compact,
                    'append_found': 'arr[taille]=valeur;' in compact and 'taille++;' in compact,
                }
            report['retrieval'].append({'case':name,'query':query,'expected_file':expected_file,'expected_pages':expected_pages,'first_expected_page_rank':min(matched) if matched else None,'duplicate_text_extras':len(hits)-len({h.content for h in hits}),'hits':[{'file':names[h.document_id],'page':h.page_no,'content':h.content,'context':h.context} for h in hits]})
    await engine.dispose()
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab-id',type=uuid.UUID,required=True)
    asyncio.run(main(parser.parse_args().lab_id))
