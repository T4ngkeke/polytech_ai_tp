"""Probe a deployed vision model on three real course pages, without indexing.

Run with .venv/bin/python scripts/check_worker_vision.py. Override
VISION_TEST_BASE_URL, VISION_TEST_MODEL, VISION_TEST_API_KEY when changing models.
Inspect saved transcriptions against the PDF: a successful API call alone is
not a quality pass. Output is diagnostic material, never added to RAG.
"""
import base64
import json
import os
import re
from pathlib import Path
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import pymupdf

CASES = [('main-3.pdf',27,'Separate left code from right explanation; preserve the entire code example including free.'),
         ('main-2.pdf',55,'Preserve superscripts, subscripts and the binary floating-point formula as LaTeX.'),
         ('main-3.pdf',54,'Preserve the complete resize-and-append example as ONE code block, including its final two lines.')]
PROMPT = '''Read the supplied slide image faithfully. Return only JSON:
{"title":"...","blocks":[{"kind":"code|text|formula|table|navigation","text":"..."}],"unreadable":[]}.
Read separate columns separately. Keep each complete code example in one block.
Transcribe code exactly, preserving punctuation and intentional errors; do not
fix or complete code. Use LaTeX for formulas. Preserve French source language.
Record navigation separately. Do not summarize, solve, invent, or omit visible
body content. Report unreadable areas explicitly. Do not include reasoning.
'''

RESPONSE_SCHEMA = {
    "type":"json_schema", "json_schema":{"name":"page_reading", "strict":True,
    "schema":{"type":"object","additionalProperties":False,
      "required":["title","blocks","unreadable"],"properties":{
        "title":{"type":"string"},
        "blocks":{"type":"array","items":{"type":"object","additionalProperties":False,
          "required":["kind","lines"],"properties":{
            "kind":{"type":"string","enum":["code","text","formula","table","navigation"]},
            "lines":{"type":"array","items":{"type":"string"}}}}},
        "unreadable":{"type":"array","items":{"type":"string"}}
      }}}}

FULL_CODE = [
    'if (taille == capacite) {', 'int nouvelle = capacite * 2;',
    'int *temp = realloc(arr, nouvelle * sizeof(*arr));',
    'if (temp == NULL) {', 'free(arr);', 'return 1;', '}',
    'arr = temp;', 'capacite = nouvelle;', '}',
    'arr[taille] = valeur;', 'taille++;',
]

def assess_response(case, response):
    """Small deterministic checks, followed by human source-image inspection.

    Whitespace-insensitive full-code comparison is only suitable for the p54
    example (it has no string literals). Never use this as general code grading.
    """
    try:
        choice=response['choices'][0]
        if choice['finish_reason']!='stop':raise ValueError('truncated or unfinished')
        raw=choice['message']['content']
        from backend.worker.context import _final_part
        final=_final_part(raw)
        if final.startswith('```'):
            final=re.sub(r'^```(?:json)?\s*|\s*```$', '', final)
        data=json.loads(final)
        if set(data)!={'title','blocks','unreadable'} or not isinstance(data['title'],str):
            raise ValueError('invalid page schema')
        if not isinstance(data['blocks'],list) or not data['blocks'] or data['unreadable']!=[]:
            raise ValueError('empty or unreadable page')
        for b in data['blocks']:
            if set(b)!={'kind','lines'} or b['kind'] not in ['code','text','formula','table','navigation']:
                raise ValueError('invalid block schema')
            if not isinstance(b['lines'],list) or not all(isinstance(l,str) for l in b['lines']):
                raise ValueError('invalid block lines')
            if any(re.search(r'</?(think|analysis|reasoning)',l,re.I) for l in b['lines']):
                raise ValueError('reasoning inside block')
        codes=['\n'.join(b['lines']) for b in data['blocks'] if b['kind']=='code']
        compact=lambda s:re.sub(r'\s+','',s)
        if case=='full_code':
            passed=any(compact(c)==compact('\n'.join(FULL_CODE)) for c in codes)
        elif case=='formula':
            formulas=' '.join(l for b in data['blocks'] if b['kind']=='formula' for l in b['lines'])
            passed=bool('biais' in formulas and 'bais' not in formulas and
                        re.search(r'2\s*\^\s*\{', formulas) and
                        re.search(r'\^\s*\{?S', formulas) and 'M' in formulas)
        elif case=='columns':
            passed=any(all(x in c for x in ['malloc','if (tab == NULL)','fprintf','for (int','free(tab)'])
                       and 'allocation peut' not in c and 'déréférence' not in c for c in codes)
            prose=' '.join(l for b in data['blocks'] if b['kind']=='text' for l in b['lines'])
            passed=passed and 'allocation peut échouer' in prose and 'trois lignes' in prose
            # Source uses visible-space markers in this literal. Accept either
            # literal spaces or those markers, never invented mathematical symbols.
            passed=passed and any('\"plus de memoire\\n\"' in c.replace('␣',' ') for c in codes)
        else:raise ValueError('unknown case')
        return {'passed':passed,'parsed':data,'reason':None if passed else 'source acceptance check failed'}
    except (ValueError,TypeError,KeyError,IndexError,AttributeError) as exc:
        return {'passed':False,'parsed':None,'reason':str(exc)}

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--structured',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=root/'documentation/worker-vision-2026-09-08';out.mkdir(exist_ok=True)
    base=os.getenv('VISION_TEST_BASE_URL','http://127.0.0.1:8001/v1').rstrip('/')
    model=os.getenv('VISION_TEST_MODEL','ornith')
    report={'model':model,'cases':[]}
    with httpx.Client(timeout=180,headers={'Authorization':'Bearer '+os.getenv('VISION_TEST_API_KEY','local')}) as client:
        for filename,page,check in CASES:
            with pymupdf.open(root/'cours'/filename) as doc:
                p=doc[page-1];pix=p.get_pixmap(matrix=pymupdf.Matrix(1600/p.rect.width,1600/p.rect.width))
                png=pix.tobytes('png')
            start=time.monotonic()
            prompt=PROMPT+'\nFocus: '+check
            if args.structured:
                prompt=prompt.replace('{\"kind\":\"code|text|formula|table|navigation\",\"text\":\"...\"}', '{\"kind\":\"code|text|formula|table|navigation\",\"lines\":[\"one physical line\"]}')
                prompt+='\nEach lines item is one real line, not a literal backslash-n-separated paragraph. Return only fields required by the schema.'
            payload={'model':model,'temperature':0,'max_tokens':4096,'messages':[{'role':'user','content':[
                {'type':'text','text':prompt},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png).decode()}}
            ]}]}
            if args.structured:
                payload['response_format']=RESPONSE_SCHEMA
                if page==55:
                    with pymupdf.open(root/'cours'/filename) as doc:
                        p=doc[page-1]
                        clip=pymupdf.Rect(0.05*p.rect.width,0.15*p.rect.height,0.95*p.rect.width,0.5*p.rect.height)
                        crop=p.get_pixmap(matrix=pymupdf.Matrix(4,4),clip=clip).tobytes('png')
                    payload['messages'][0]['content'].extend([{'type':'text','text':'Magnified formula region from the SAME page:'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(crop).decode()}}])
            result={'file':filename,'page':page,'check':check}
            try:
                resp=client.post(base+'/chat/completions',json=payload)
                result.update(http_status=resp.status_code,seconds=round(time.monotonic()-start,2),response=resp.json())
            except (httpx.HTTPError,ValueError) as exc:
                result.update(error=type(exc).__name__,seconds=round(time.monotonic()-start,2))
            if args.structured and result.get('http_status')==200:
                case='formula' if page==55 else ('columns' if page==27 else 'full_code')
                result['assessment']=assess_response(case,result['response'])
            report['cases'].append(result)
            (out/('structured-probe.json' if args.structured else 'probe.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(filename,'page',page,'HTTP',result.get('http_status'),str(result.get('error','')),flush=True)
    print('Saved:',out/('structured-probe.json' if args.structured else 'probe.json'),flush=True)

if __name__=='__main__':main()
