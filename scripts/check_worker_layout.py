"""Serial, resumable visual audit. No DB writes and no raw model reasoning.

A resumed run must match all inputs, code and options. Failed pages are recorded
once per round; reruns require a separate run directory, never overwrite history.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openai import AsyncOpenAI
from backend.worker.page_reader import make_page_reader,chunk_read_pages,extract_native_pages

FOCUS=[('main-2.pdf',6),('main-2.pdf',55),('main-3.pdf',27),('main-3.pdf',54),
       ('Heritage.pdf',2),('Heritage.pdf',8),('Heritage.pdf',13),('Heritage.pdf',15)]


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_resume_manifest(saved, current):
    """A resumed run continues the same experiment: every recorded input must match."""
    changed=[k for k in sorted(set(saved)|set(current)) if saved.get(k)!=current.get(k)]
    if changed:
        raise ValueError('Resume refused; changed: '+', '.join(changed))


def git_commit(root):
    import subprocess
    run=lambda *args:subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True).stdout.strip()
    # Tracked code changes make the commit alone insufficient; the code hashes still identify it.
    return run('rev-parse','HEAD')+('-dirty' if run('status','--porcelain','--','backend','scripts') else '')


def aggregate(rows):
    traces=[t for r in rows for p in r.get('audit',{}).get('pages',[]) for t in p.get('trace',[])]
    traces += [t for r in rows if not r.get('audit',{}).get('pages')
               for p in r.get('audit',{}).get('page_results',[]) for t in p.get('trace',[])]
    per_page_traces=[p.get('trace',[]) for r in rows for p in (r.get('audit',{}).get('pages') or r.get('audit',{}).get('page_results',[]))]
    confirmed=sum(any(p.get('reason')=='source_defect_confirmed' for p in r.get('audit',{}).get('page_results',[])) for r in rows)
    accepted=sum(r['status']=='accepted' for r in rows)
    return {'confirmed_source_defects':confirmed,
        'whole_document_available_rate':accepted/len(rows) if rows else None,
        'worker_rate_excluding_confirmed_defects':accepted/(len(rows)-confirmed) if len(rows)>confirmed else None,
        'pages':len(rows),'accepted':sum(r['status']=='accepted' for r in rows),
        'calls':len(traces),'output_chars':sum(t.get('output_chars',0) for t in traces),
        'truncations':sum(t.get('finish_reason')=='length' or 'normally: length' in t.get('error','') for t in traces),
        'repeated_rejections':sum(bool(t.get('error')) and t.get('error')==prev.get('error') for pp in per_page_traces for prev,t in zip(pp,pp[1:])),
        'partial':sum(r['status']=='partial' for r in rows),
        'needs_review':sum(r['status']=='needs_review' for r in rows),
        'review_tasks':sum(len(r.get('audit',{}).get('reviews',[])) for r in rows),
        'usable_page_rate':sum(r['status'] in ('accepted','partial') for r in rows)/len(rows) if rows else None,
        'request_failures':sum(r['status']=='failed' for r in rows),
        'seconds':round(sum(r['seconds'] for r in rows),2),
        'review_reasons':dict(Counter(v.get('reason_code','unrecorded') for r in rows for v in r.get('audit',{}).get('reviews',[]))),
        'page_failure_reasons':dict(Counter(p['reason'] for r in rows for p in r.get('audit',{}).get('page_results',[]) if p.get('reason'))),
        'source_geometry_unverified':[{'file':r['file'],'page':r['page']} for r in rows
            if any(p.get('reason')=='source_geometry_unverified' for p in r.get('audit',{}).get('page_results',[]))]}


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--all',action='store_true')
    parser.add_argument('--document')
    parser.add_argument('--case',action='append')
    parser.add_argument('--workers',type=int,choices=[1],default=1,help='Local model audit must be serial')
    parser.add_argument('--harness',choices=['v3','v4','region'],default='v4')
    parser.add_argument('--full-retry',action='store_true',help='V4 control: resend whole page, no group correction')
    parser.add_argument('--frequency-penalty',type=float,choices=[0.0,0.2,0.5])
    parser.add_argument('--repeat',type=int,choices=[1,2],default=1)
    parser.add_argument('--resume',type=Path)
    args=parser.parse_args()
    if args.harness=='region' and (args.full_retry or args.frequency_penalty is not None):
        parser.error('Region comparison uses unchanged default parameters, no full-retry mode')
    root=Path(__file__).resolve().parents[1]
    paths={p.name:p for p in sorted((root/'cours').glob('*.pdf'))}
    pages={name:extract_native_pages(str(p),geometry_hints=args.harness!='v3',isolate_errors=args.harness=='region') for name,p in paths.items()}
    cases=FOCUS
    if args.case:cases=[(x.rsplit(':',1)[0],int(x.rsplit(':',1)[1])) for x in args.case]
    if args.all:cases=[(name,p.page_no) for name,pp in pages.items() for p in pp]
    if args.document:cases=[c for c in cases if c[0]==args.document]
    if not cases or len(set(cases))!=len(cases):parser.error('Select nonempty unique pages')
    if any(n not in pages or p<1 or p>len(pages[n]) for n,p in cases):parser.error('Unknown file or physical page')
    base=os.environ.get('VISION_TEST_BASE_URL','http://127.0.0.1:8001/v1')
    model=os.environ.get('VISION_TEST_MODEL','ornith')
    review_path=root/'documentation/worker-visual-2026-09-09/source-reviews.json'
    reviews=json.loads(review_path.read_text()) if review_path.exists() else []
    async with AsyncOpenAI(base_url=base,api_key=os.environ.get('VISION_TEST_API_KEY','local'),timeout=120,max_retries=0) as client:
        available=await client.models.list()
        if model not in [m.id for m in available.data]:raise SystemExit('Configured model not present at endpoint')
        served=next(m for m in available.data if m.id==model)
        manifest={'source_reviews_sha256':digest(review_path) if review_path.exists() else None,'cases':[list(c) for c in cases],'files':{n:digest(paths[n]) for n in sorted({n for n,p in cases})},
            'code':{str(p.relative_to(root)):digest(p) for p in sorted((root/'backend/worker').glob('*.py'))},
            'script_sha256':digest(Path(__file__)),'git_commit':git_commit(root),'harness':args.harness,'group_correction':not args.full_retry,
            'frequency_penalty':args.frequency_penalty,'rounds':args.repeat,'model':model,'base_url':base,
            # The API names the served model only; weights are identified by the operator, if at all.
            'model_service':{'id':served.id,'root':getattr(served,'root',None),'max_model_len':getattr(served,'max_model_len',None)},
            'model_weights':os.environ.get('VISION_TEST_MODEL_WEIGHTS'),
            'temperature':0,'max_tokens':8192,'timeout':120,'workers':1}
        out=args.resume or root/'documentation/worker-visual-2026-09-09'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if args.resume:
            try:validate_resume_manifest(json.loads((out/'manifest.json').read_text()),manifest)
            except ValueError as exc:raise SystemExit(str(exc)) from None
        else:
            out.mkdir(parents=True,exist_ok=False)
            (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        print('Report:',out,flush=True)
        if args.harness=='region':
            from backend.worker.region_reader import make_region_reader
            reader=make_region_reader(client,model)
        else:
            reader=make_page_reader(client,model,harness=args.harness,group_correction=not args.full_retry,frequency_penalty=args.frequency_penalty)
        results=[]
        for round_no in range(1,args.repeat+1):
            for name,number in cases:
                output=out/f'round-{round_no}-{name}-p{number}.json'
                if output.exists():results.append(json.loads(output.read_text()));continue
                if digest(paths[name])!=manifest['files'][name]:raise RuntimeError('Source changed during audit')
                if any(digest(root/rel)!=h for rel,h in manifest['code'].items()):raise RuntimeError('Worker code changed during audit')
                started=time.monotonic()
                row={'file':name,'page':number,'round':round_no,'model':model,'source_file_sha256':manifest['files'][name]}
                # Unexpected exceptions terminate the audit visibly; they are not
                # converted into an ordinary "page unreadable" result.
                read,audit=await reader(str(paths[name]),page_numbers={number},prepared_pages=pages[name])
                for outcome in audit['page_results']:
                    proof=next((r for r in reviews if r['file']==name and r['page']==number and r['sha256']==manifest['files'][name] and r['status']=='confirmed_source_defect'),None)
                    if proof and outcome.get('reason')=='source_geometry_unverified':
                        outcome.update(reason='source_defect_confirmed',source_review=proof)
                row.update(status=('accepted' if audit['page_results'][0]['status']=='read' else audit['page_results'][0]['status']) if args.harness=='region' else ('accepted' if read else audit['page_results'][0]['status']),audit=audit,
                           chunks=[asdict(c) for c in chunk_read_pages(read,file_sha256=manifest['files'][name])],seconds=round(time.monotonic()-started,3))
                temp=output.with_suffix('.tmp');temp.write_text(json.dumps(row,ensure_ascii=False,indent=2));temp.replace(output)
                results.append(row)
                print(json.dumps({k:v for k,v in row.items() if k not in ('audit','chunks')}),flush=True)
                (out/'summary.json').write_text(json.dumps(aggregate(results),indent=2))
        (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(aggregate(results)),flush=True)


if __name__=='__main__':asyncio.run(main())
