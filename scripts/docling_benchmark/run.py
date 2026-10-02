"""Run one serial variant/round. Outputs are immutable and resumable with identical inputs."""
import argparse
import contextlib
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import platform
import subprocess
import time
import urllib.request
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

import pymupdf

from .adapters import DoclingAdapter, WorkerAdapter, PICTURE_PROMPT
from .marker_adapter import MarkerAdapter, VARIANTS as MARKER_VARIANTS
from .mineru_adapter import MinerUAdapter, VARIANTS as MINERU_VARIANTS
from .mineru_review import MinerUReviewAdapter, VARIANTS as MINERU_REVIEW_VARIANTS
from .paddle_adapter import PaddleAdapter, VARIANTS as PADDLE_VARIANTS
from .suite import ROOT, OUTPUT, load_suite, freeze, code_fingerprint, source_path
from .telemetry import ModelMeter

VARIANT_CHOICES = ('worker', 'legacy-c-api', 'patched-formula', 'patched-no-formula',
                   *MARKER_VARIANTS, *MINERU_VARIANTS, *MINERU_REVIEW_VARIANTS, *PADDLE_VARIANTS)
SIDE_SERVERS = {'marker-balanced': ('edullm-marker-surya', '18002'),
                'marker-balanced-llm': ('edullm-marker-surya', '18002'),
                'mineru-hybrid': ('edullm-mineru-vlm', '18003'),
                'paddle-vl': ('edullm-paddle-vl', '18004')}
SERVER_TAGS = {'18002': 'surya', '18003': 'mineru-vlm', '18004': 'paddle-vl'}


def side_server(variant):
    """The local VLM container and port a variant needs besides Ornith, or None."""
    return SIDE_SERVERS.get(variant)


def stable_models(models):
    """The served model list without vLLM's `created` field, which is just the server's current time."""
    return dict(models, data=[{k: v for k, v in m.items() if k != 'created'} for m in models['data']])


async def run(args):
    suite = load_suite(args.suite) if args.suite else load_suite()
    args.output.mkdir(parents=True,exist_ok=True)
    lock = (args.output/'serial.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with urllib.request.urlopen(args.base_url+'/models',timeout=10) as response:
        models=json.load(response)
    if args.model not in [m['id'] for m in models['data']]:
        raise ValueError('Expected model is not served')
    services=subprocess.check_output(['docker','ps','--format','{{.Names}} {{.Ports}}'],text=True)
    required={'edullm-frontend':'8080','edullm-backend':'8000','edullm-db':'5432','edullm-docling-compare-ornith':'18001'}
    if side_server(args.variant):
        name,port=side_server(args.variant);required[name]=port
    for name,port in required.items():
        if not any(line.split()[0]==name and f':{port}->' in line for line in services.splitlines()):
            raise ValueError(f'Expected container/port missing: {name}:{port}')
    packages={}
    for name in ('docling','docling-core','docling-ibm-models','marker-pdf','surya-ocr','mineru','mineru-vl-utils',
                 'paddleocr','paddlex','paddlepaddle','torch','transformers','pymupdf','openai'):
        try: packages[name]=version(name)
        except PackageNotFoundError: packages[name]=None
    config={'version':'benchmark-v9.1-1','variant':args.variant,'round':args.round,
            'base_url':args.base_url,'model':args.model,'models':stable_models(models),'python':platform.python_version(),
            'packages':packages,'code':code_fingerprint(),'suite':suite,'picture_prompt':PICTURE_PROMPT,
            'selection':args.pages,'hardware':platform.machine(),
            'environment':{k:os.environ.get(k) for k in ('HF_HOME','CPATH','MODEL_CACHE_DIR','HF_HUB_OFFLINE',
                                                          'MINERU_TOOLS_CONFIG_JSON','PADDLE_PDX_CACHE_HOME')}}
    if args.variant in MINERU_REVIEW_VARIANTS:
        # A second step over the stored, already graded MinerU hybrid output of the same round.
        source_run=args.output/'runs'/f'mineru-hybrid-round{args.round}'
        if not (source_run/'config.json').exists():
            raise ValueError(f'MinerU source run missing: {source_run}')
        config['source']={'run':source_run.name,'config_sha256':hashlib.sha256((source_run/'config.json').read_bytes()).hexdigest()}
    folder=args.output/'runs'/f'{args.variant}-round{args.round}'
    freeze(folder,config)
    (folder/'services.txt').write_text(services)
    (args.output/'suite.json').write_text(json.dumps(suite,ensure_ascii=False,indent=2))
    selected=[c for c in suite['cases'] if not args.pages or c['stem'] in args.pages]
    if args.pages and set(args.pages)!={c['stem'] for c in selected}:
        raise ValueError('Unknown page selection')
    with contextlib.ExitStack() as stack:
        meter=stack.enter_context(ModelMeter(args.base_url))
        # A variant's own VLM server (surya, MinerU2.5, PaddleOCR-VL) is metered separately so its calls count too.
        # marker-text keeps a surya meter only because the Marker adapter always takes the URL; it makes no calls.
        side_port=side_server(args.variant)[1] if side_server(args.variant) else ('18002' if args.variant in MARKER_VARIANTS else None)
        surya=stack.enter_context(ModelMeter(f'http://127.0.0.1:{side_port}/v1')) if side_port else None
        if args.variant=='worker':adapter=WorkerAdapter(meter.url,args.model)
        elif args.variant in MARKER_VARIANTS:adapter=MarkerAdapter(args.variant,meter.url,surya.url,args.model)
        elif args.variant in MINERU_REVIEW_VARIANTS:adapter=MinerUReviewAdapter(args.variant,source_run,meter.url,args.model)
        elif args.variant in MINERU_VARIANTS:adapter=MinerUAdapter(args.variant,surya.url if surya else None)
        elif args.variant in PADDLE_VARIANTS:adapter=PaddleAdapter(args.variant,surya.url if surya else None)
        else:adapter=DoclingAdapter(args.variant,meter.url,args.model)
        (folder/'initialization.json').write_text(json.dumps({'seconds':adapter.initialization_seconds,'options':getattr(adapter,'options_record',adapter.options)},indent=2))
        for case in selected:
            target=folder/(case['stem']+'.page.json')
            if target.exists():
                continue
            if code_fingerprint()!=config['code']:
                raise ValueError('Code changed during run')
            path=source_path(case['file'])
            events_start=len(meter.events);surya_start=len(surya.events) if surya else 0
            started=time.monotonic()
            stage_errors=[]
            class StageErrors(logging.Handler):
                def emit(self, record):
                    if record.levelno>=logging.ERROR:
                        stage_errors.append({'kind':'internal_stage_error','logger':record.name,'message':record.getMessage()})
            handler=StageErrors()
            logging.getLogger().addHandler(handler)
            try:
                row,raw=await adapter.read(path,case['page'],suite['files'][case['file']])
            except Exception as exc:
                # Unexpected errors are recorded distinctly and stop the run. Never masquerade as unreadability.
                (folder/(case['stem']+'.fatal.json')).write_text(json.dumps({'kind':type(exc).__name__,'error':str(exc)},indent=2))
                raise
            finally:
                logging.getLogger().removeHandler(handler)
            row.errors.extend(stage_errors)
            row.metadata['requests']=row.metadata.pop('source_requests',[])+[dict(e,server='ornith') for e in meter.events[events_start:]]+\
                ([dict(e,server=SERVER_TAGS[side_port]) for e in surya.events[surya_start:]] if surya else [])
            row.calls=len(row.metadata['requests'])
            row.usage=[e['usage'] for e in row.metadata['requests'] if e.get('usage')]
            # Serialization/export is included; terminal result-file write itself is negligible and excluded.
            (folder/(case['stem']+'.raw.json')).write_text(json.dumps(raw,ensure_ascii=False,indent=2))
            # A second-step variant also counts the stored MinerU seconds of the page it builds on.
            row.seconds=time.monotonic()-started+row.metadata.get('source_seconds',0)
            tmp=target.with_suffix('.tmp')
            tmp.write_text(json.dumps(row.dump(),ensure_ascii=False,indent=2));tmp.replace(target)
            previews=args.output/'previews';previews.mkdir(exist_ok=True)
            image=previews/(case['stem']+'.png')
            if not image.exists():
                with pymupdf.open(path) as pdf:
                    pdf[case['page']-1].get_pixmap(matrix=pymupdf.Matrix(2,2)).save(image)
            print(json.dumps({'page':case['stem'],'status':row.status,'seconds':round(row.seconds,2),'calls':row.calls,'reviews':len(row.reviews)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--variant',choices=VARIANT_CHOICES,required=True)
    p.add_argument('--round',type=int,choices=[1,2],required=True)
    p.add_argument('--base-url',default='http://127.0.0.1:18001/v1')
    p.add_argument('--model',default='ornith')
    p.add_argument('--output',type=Path,default=OUTPUT)
    p.add_argument('--pages',nargs='*')
    p.add_argument('--suite',type=Path,help='Folder with manifest.json and checks.json (default: the 28-page development set)')
    asyncio.run(run(p.parse_args()))
