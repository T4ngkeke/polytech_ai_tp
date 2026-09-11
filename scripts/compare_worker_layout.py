"""Compare matched serial runs without hiding failures behind faster averages."""
import json
from pathlib import Path
import sys


def key(r):return r['file'],r['page'],r.get('round',1)


def calls(r):
    audit=r.get('audit',{})
    if audit.get('pages'):return sum(p.get('calls',len(p.get('trace',[]))) for p in audit['pages'])
    return sum(len(p.get('trace',[])) for p in audit.get('page_results',[]))


def compare(before,after):
    a={key(r):r for r in before};b={key(r):r for r in after}
    if len(a)!=len(before) or len(b)!=len(after) or set(a)!=set(b):raise ValueError('Runs need identical unique page/round sets')
    common=[k for k in a if a[k]['status']==b[k]['status']=='accepted']
    mean=lambda rows:sum(calls(rows[k]) for k in common)/len(common) if common else None
    ma,mb=mean(a),mean(b)
    successes=lambda rows:sum(r['status']=='accepted' for r in rows.values())
    rounds=sorted({k[2] for k in a})
    no_regression=all(sum(r['status']=='accepted' for k,r in b.items() if k[2]==n)>=
                      sum(r['status']=='accepted' for k,r in a.items() if k[2]==n) for n in rounds)
    return {'before_accepted':successes(a),'after_accepted':successes(b),'page_runs':len(a),
        'common_success_pages':common,'common_success_mean_calls':{'before':ma,'after':mb},
        'before_total_calls':sum(map(calls,before)),'after_total_calls':sum(map(calls,after)),
        'regressed_pages':[k for k in a if a[k]['status']=='accepted' and b[k]['status']!='accepted'],
        'mechanical_gate_passed':bool(common) and mb<ma and no_regression,
        'manual_content_gate':'pending: mechanical success does not establish correctness'}


if __name__=='__main__':
    left,right=map(Path,sys.argv[1:3])
    manifests=[json.loads((p/'manifest.json').read_text()) for p in (left,right)]
    for field in ('cases','files','code','script_sha256','rounds','model','base_url','temperature','max_tokens','timeout','workers','frequency_penalty'):
        if manifests[0][field]!=manifests[1][field]:raise SystemExit(f'Unmatched comparison: {field}')
    result=compare(json.loads((left/'results.json').read_text()),json.loads((right/'results.json').read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
