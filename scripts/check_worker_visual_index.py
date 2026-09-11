"""Replay a completed single-round visual audit into an isolated test course, with real embeddings.

Requires WORKER_TEST_DB, EMBED_TEST_URL, and the final scan results.json path.
Never drops tables or changes production model settings. No chat/context LLM calls.
"""
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openai import AsyncOpenAI
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from backend.app.database import Base
from backend.app.models import User,UserRole,Class,Lab,DocChunk
from backend.app.services.document_service import create_document
from backend.app.services.retrieval_service import hybrid_search
from backend.worker.ingest import ingest_document
from backend.worker.main import _make_real_embed_fn
from backend.worker.page_reader import NativeRegion,ReadGroup,ReadPage,extract_native_pages
from scripts.check_course_worker_quality import CASES


def cached_embed(remote,cache):
    """Reuse a vector only for byte-identical text. Order and duplicates are kept:
    a misplaced vector would attach to the wrong chunk and void the audit.
    `cache` is filled in place so a run can be saved and reused by the next one."""
    async def embed(texts):
        missing=list(dict.fromkeys(t for t in texts if t not in cache))
        if missing:
            vectors=await remote(missing)
            if len(vectors)!=len(missing):raise ValueError('Embedding count mismatch')
            cache.update(zip(missing,vectors))
        return [cache[t] for t in texts]
    return embed


def validate_audit_protocol(records):
    if any(r.get('audit', {}).get('version', '').startswith('region-reader-') for r in records):
        raise ValueError('Region audit needs review persistence before indexing; offline only')


async def main():
    path=Path(sys.argv[1]).resolve()
    records=json.loads(path.read_text())
    validate_audit_protocol(records)
    assert records and all(r.get('round',1)==1 for r in records), 'Use a completed single-round full scan'
    assert len({(r['file'],r['page']) for r in records})==len(records), 'Duplicate audit pages'
    url=os.environ['WORKER_TEST_DB']
    from sqlalchemy.engine import make_url
    parsed=make_url(url)
    assert parsed.database=='edudb_worker_test' and parsed.host=='127.0.0.1' and parsed.port==5432
    engine=create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        await conn.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    root=Path(__file__).resolve().parents[1]
    report={'scan':str(path),'context_llm_used':False,'embedding_model':'bge-m3:latest','documents':[],'retrieval':[]}
    async with AsyncOpenAI(base_url=os.environ['EMBED_TEST_URL'],api_key='ollama',timeout=180,max_retries=0) as client:
        embed=cached_embed(_make_real_embed_fn(client,'bge-m3:latest'),{})
        async with factory() as db:
            teacher=User(username='visual-audit-'+uuid.uuid4().hex,hashed_password='!disabled',role=UserRole.teacher)
            db.add(teacher);await db.flush()
            cls=Class(name='Worker visual audit',teacher_id=teacher.id,invite_code=uuid.uuid4().hex[:6]);db.add(cls);await db.flush()
            lab=Lab(name='Two PDFs / partial pages',class_id=cls.id);db.add(lab);await db.commit()
            report['lab_id']=str(lab.id)
            names={}
            for name in sorted({r['file'] for r in records}):
                source=root/'cours'/name
                originals=extract_native_pages(str(source),geometry_hints=True)
                count=len(originals)
                digest=hashlib.sha256(source.read_bytes()).hexdigest()
                selected=sorted((r for r in records if r['file']==name),key=lambda r:r['page'])
                assert [r['page'] for r in selected]==list(range(1,count+1))
                assert all(r.get('source_file_sha256',digest)==digest for r in selected), 'Original file changed since audit'
                report.setdefault('legacy_audits_without_file_hash',{})[name]=sum('source_file_sha256' not in r for r in selected)
                pages=[];outcomes=[]
                for row in selected:
                    n=row['page']; audit=row.get('audit',{})
                    outcome=dict(audit.get('page_results',[{'status':'failed','error':row.get('error')}])[0],page_no=n)
                    outcomes.append(outcome)
                    if not audit.get('pages'):continue
                    saved=audit['pages'][0];original=originals[n-1]
                    is_v4=saved.get('reader_version','').startswith('visual-harness-v4')
                    if is_v4:assert row.get('source_file_sha256')==digest, 'V4 requires exact file hash'
                    # Single-page scan IDs use p1. Verify against the full PDF
                    # before remapping; never attach an unrelated audit to a source.
                    assert [l['text'] for l in saved['source']['lines']]==[l.text for l in original.lines]
                    assert all(all(abs(a-b)<.02 for a,b in zip(l['bbox'],o.bbox)) for l,o in zip(saved['source']['lines'],original.lines))
                    remap=lambda i:i if is_v4 else re.sub(r'^p1(?=[lrv])',f'p{n}',i)
                    regions=tuple(NativeRegion(**{**r,'id':remap(r['id']),'bbox':tuple(r['bbox']),
                        'source_ids':tuple(map(remap,r.get('source_ids',[])))}) for r in saved['source'].get('regions',[]))
                    from dataclasses import replace
                    native=replace(original,regions=regions)
                    groups=tuple(ReadGroup(g['kind'],tuple(map(remap,g['ids'])),g['text'],tuple(map(remap,g.get('region_ids',[]))),g.get('visual')) for g in saved['groups'])
                    pages.append(ReadPage(native,groups,saved['calls'],tuple(saved.get('trace',[])),
                        tuple(saved.get('observations',[])),tuple(saved.get('decorations',[])),saved.get('reader_version','visual-layout-v3.2')))
                audit={'version':selected[0].get('audit',{}).get('version','unknown'),'source_sha256':digest,'model':'ornith','page_count':count,'page_results':outcomes,'pages':[asdict(p) for p in pages]}
                async def replay(_path):return pages,audit
                doc=await create_document(db,class_id=cls.id,lab_id=lab.id,filename=name,content=source.read_bytes(),uploaded_by=teacher.id,storage_root='/tmp/worker-visual-index-docs')
                await db.commit()
                await ingest_document(db,doc.id,embed_fn=embed,page_reader_fn=replay)
                names[doc.id]=name
                chunks=list((await db.scalars(select(DocChunk).where(DocChunk.document_id==doc.id))).all())
                report['documents'].append({'file':name,'id':str(doc.id),'status':doc.status.value,'page_count':doc.page_count,'summary':doc.ingest_report.get('page_summary'),'chunks':len(chunks),'vectors_valid':all(len(c.embedding)==1024 for c in chunks),'provenance_present':all(c.source_ref and c.source_text is not None for c in chunks)})
                print(json.dumps(report['documents'][-1]),flush=True)
            extra=[('formula','Quelle formule utilise le signe, la mantisse et l’exposant pour représenter un nombre flottant ?','main-2.pdf',[55])]
            if 'Heritage.pdf' in names.values():
                extra.append(('inheritance','De quelle classe Manager hérite-t-il ?', 'Heritage.pdf',[8]))
            cases=[c for c in CASES+extra if c[2] in names.values()]
            vectors=await embed([c[1] for c in cases])
            for (case,query,file,expected),vector in zip(cases,vectors):
                hits,_=await hybrid_search(db,query,vector,lab.id,class_id=cls.id,language='fr',top_k=5)
                ranks=[i+1 for i,h in enumerate(hits) if names[h.document_id]==file and h.page_no in expected]
                if case=='full_example':
                    content=''.join(h.content for h in hits if names[h.document_id]==file and h.page_no==54)
                    compact=re.sub(r'\s+','',content)
                    report['full_example_parts']={'resize_block_found':'if(taille==capacite)' in compact and 'realloc(arr,' in compact, 'append_found':'arr[taille]=valeur;' in compact and 'taille++;' in compact}
                report['retrieval'].append({'case':case,'expected_file':file,'expected_pages':expected,'rank':min(ranks) if ranks else None,'hits':[{'file':names[h.document_id],'page':h.page_no,'content':h.content} for h in hits]})
            alien,_=await hybrid_search(db,'allocation',vectors[0],uuid.uuid4(),class_id=uuid.uuid4(),top_k=5)
            report['foreign_scope_empty']=not alien
    await engine.dispose()
    output=path.parent/'index-results.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Report:',output,flush=True)


if __name__=='__main__':asyncio.run(main())
