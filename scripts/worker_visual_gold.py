"""Narrow content checks for the reviewed fixtures; not a semantic quality score.

False means this check did not establish preservation, and requires review. A
true result never replaces the full image/content audit. File hashes are listed
with CLI output so the checked artifact is unambiguous.
"""
import json
import re
import sys
from pathlib import Path


def check_page(row):
    groups=[g for p in row.get('audit',{}).get('pages',[]) for g in p['groups']]
    text='\n'.join(g['text'] for g in groups)
    compact=re.sub(r'\s+','',text)
    key=row['file'],row['page']
    if key==('main-2.pdf',55):
        formulas=[g['text'] for g in groups if g.get('kind')=='formula' and g.get('visual')]
        normal=''.join(formulas).replace('−','-')
        normal=re.sub(r'\s+|\\left|\\right','',normal)
        return {'formula_structure_preserved':bool(re.search(r'2\^\{E-biais\}',normal)) and
                    bool(re.search(r'\(-1\)\^(?:\{S\}|S)',normal)),
                'bit_width_labels_present':all(x in text for x in ('1 bit','8 bit','23 bit'))}
    if key==('main-3.pdf',27):return {'original_space_symbol_preserved':'plus␣de␣memoire' in text}
    if key==('main-3.pdf',54):return {
        'resize_and_append_present':all(x in compact for x in ('if(taille==capacite)','realloc(arr,','arr[taille]=valeur;','taille++;')),
        'code_group_preserved':any(g['kind']=='code' for g in groups)}
    if key==('Heritage.pdf',8):
        # UML is a figure: a short description plus the class names as printed.
        figures=[g for g in groups if g.get('kind')=='figure' and (g.get('visual') or {}).get('description','').strip()]
        return {'figure_described':bool(figures),
                'class_names_kept':all(x in text for x in ('Employee','Manager','Programmer'))}
    return {'manual_review_required':True}


CASES=Path(__file__).resolve().parents[1]/'backend/tests/fixtures/worker_visual_cases.json'
# What a person may write for each check, and which answers pass.
ANSWERS=('correct','reviewed','wrong','missing')
PASSING={'native_code':{'correct'},'text':{'correct'},'visual_fact':{'correct','reviewed'},
         'review_quality':{'correct','wrong'}}


def load_cases():
    return json.loads(CASES.read_text())


def output_digest(row):
    """What was indexed and what was sent to review; a new output voids old checks."""
    import hashlib
    audit=row.get('audit',{})
    value={'status':row.get('status'),
           'groups':[[g.get('kind'),list(g.get('ids',[])),g.get('text')] for p in audit.get('pages',[]) for g in p.get('groups',[])],
           'reviews':[[list(r.get('ids',[])),list(r.get('region_ids',[])),r.get('reason_code'),r.get('reason'),
                       r.get('candidate_content')] for r in audit.get('reviews',[])]}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def _case(cases,row):
    return next((c for c in cases['focus'] if (c['file'],c['page'])==(row['file'],row['page'])),None)


def checklist(results,*,run,cases=None):
    """A form for a person. It is never marked as human-checked by the program."""
    cases=cases or load_cases()
    items=[]
    for row in results:
        case=_case(cases,row)
        if case is None:
            continue
        audit=row.get('audit',{})
        items.append({'run':run,'file':row['file'],'page':row['page'],'round':row.get('round',1),
            'source_sha256':row.get('source_file_sha256'),'output_sha256':output_digest(row),
            'reviewer':None,'human':False,'checks':case['checks'],'results':{c['id']:None for c in case['checks']},
            'invented_facts':[],'unnecessary_reviews':0,'notes':'',
            'answers':'correct | reviewed (sent to a teacher) | wrong | missing (silently lost)',
            'output':{'status':row.get('status'),
                      'groups':[{'kind':g.get('kind'),'text':g.get('text')} for p in audit.get('pages',[]) for g in p.get('groups',[])],
                      'reviews':[{'reason_code':r.get('reason_code'),'reason':r.get('reason'),
                                  'candidate_content':r.get('candidate_content')} for r in audit.get('reviews',[])]}})
    return items


def evaluate_content_gate(results,content_reviews,*,run,cases=None):
    """Pass only with a current human record for every focus page and no loss or error."""
    cases=cases or load_cases()
    records={(r['file'],r['page'],r.get('round',1)):r for r in content_reviews
             if r.get('human') is True and r.get('run')==run}
    pages=[];totals={'important_errors':0,'silent_omissions':0,'sent_to_review':0,'invented_facts':0,
                     'unnecessary_reviews':0,'code_not_preserved':0}
    for row in results:
        key=(row['file'],row['page'],row.get('round',1))
        case=_case(cases,row)
        record=records.get(key)
        entry={'file':key[0],'page':key[1],'round':key[2]}
        if case is None:
            state='no_checklist'
        elif record is None:
            state='pending'
        elif record.get('source_sha256')!=row.get('source_file_sha256') or record.get('output_sha256')!=output_digest(row):
            state='stale'
        else:
            values=record.get('results') or {}
            kinds={c['id']:c['kind'] for c in case['checks']}
            if set(values)!=set(kinds) or any(v not in ANSWERS for v in values.values()):
                state='incomplete'
            else:
                ok=not record.get('invented_facts')
                for check,value in values.items():
                    kind=kinds[check]
                    if kind=='review_quality':
                        totals['unnecessary_reviews']+=value=='wrong'
                    elif value=='wrong':totals['important_errors']+=1
                    elif value=='missing':totals['silent_omissions']+=1
                    elif value=='reviewed':
                        totals['sent_to_review']+=kind=='visual_fact'
                        totals['code_not_preserved']+=kind!='visual_fact'
                    ok=ok and value in PASSING[kind]
                totals['invented_facts']+=len(record.get('invented_facts') or [])
                totals['unnecessary_reviews']+=int(record.get('unnecessary_reviews') or 0)
                state='passed' if ok else 'failed'
        pages.append({**entry,'state':state})
    checked=[p for p in pages if p['state']!='no_checklist']
    return {'run':run,'passed':bool(checked) and all(p['state']=='passed' for p in checked),'pages':pages,**totals}


if __name__=='__main__' and len(sys.argv)>1 and sys.argv[1] in ('checklist','gate'):
    run_dir=Path(sys.argv[2])
    rows=json.loads((run_dir/'results.json').read_text())
    if sys.argv[1]=='checklist':
        target=run_dir/'content-review-template.json'
        if target.exists():raise SystemExit(f'{target} exists; never overwrite a checklist')
        target.write_text(json.dumps(checklist(rows,run=run_dir.name),ensure_ascii=False,indent=2))
        print('Checklist for a person:',target)
    else:
        print(json.dumps(evaluate_content_gate(rows,json.loads(Path(sys.argv[3]).read_text()),run=run_dir.name),
                         ensure_ascii=False,indent=2))
elif __name__=='__main__':
    records=json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps([{'file':r['file'],'page':r['page'],'round':r.get('round',1),
        'source_sha256':r.get('source_file_sha256'),'checks':check_page(r)} for r in records],ensure_ascii=False,indent=2))
