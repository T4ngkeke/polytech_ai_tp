"""Compare matched serial runs without hiding failures behind faster averages.

A comparison may change code, prompts and reader version; it may not change the
samples, rounds, files, model or inference parameters. Code versions are always
recorded, and missing identity evidence is reported as a limitation.
"""
import json
from pathlib import Path
import sys

SAME = ('cases', 'files', 'rounds', 'model', 'base_url', 'temperature', 'max_tokens', 'timeout', 'workers',
        'frequency_penalty')


def validate_comparison_manifests(before, after):
    for field in SAME:
        if before.get(field) != after.get(field):
            raise ValueError(f'Unmatched comparison: {field}')
    for side in (before, after):
        if not side.get('code') or not side.get('script_sha256'):
            raise ValueError('Each run must record its code and script hashes')
    limitations = []
    for field, note in (('model_service', 'Served model identity (id, root, context) was not recorded for both runs'),
                        ('model_weights', 'Model weights were not identified for both runs; the same served name is not proof of the same weights')):
        if before.get(field) and after.get(field):
            if before[field] != after[field]:
                raise ValueError(f'Unmatched comparison: {field}')
        else:
            limitations.append(note)
    if not (before.get('git_commit') and after.get('git_commit')):
        limitations.append('Git commit not recorded for both runs; the code hashes identify the code')
    return {'code_changed': before['code'] != after['code'] or before['script_sha256'] != after['script_sha256'],
            'versions': {'before': before.get('git_commit'), 'after': after.get('git_commit')},
            'harness': {'before': before.get('harness'), 'after': after.get('harness')},
            'limitations': limitations}


def key(r):return r['file'],r['page'],r.get('round',1)


def calls(r):
    audit=r.get('audit',{})
    if audit.get('pages'):return sum(p.get('calls',len(p.get('trace',[]))) for p in audit['pages'])
    return sum(len(p.get('trace',[])) for p in audit.get('page_results',[]))


def compare(before,after,content=None):
    """content: (before, after) maps of page key -> human content gate passed."""
    a={key(r):r for r in before};b={key(r):r for r in after}
    if len(a)!=len(before) or len(b)!=len(after) or set(a)!=set(b):raise ValueError('Runs need identical unique page/round sets')
    correct=(lambda k:content[0].get(k) is True and content[1].get(k) is True) if content else (lambda k:True)
    common=[k for k in a if a[k]['status']==b[k]['status']=='accepted' and correct(k)]
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
        # Efficiency is claimed only on pages a person found correct in both runs.
        'efficiency_target_met':(bool(common) and mb<ma) if content else None,
        'manual_content_gate':'applied' if content else 'pending: mechanical success does not establish correctness'}


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    accept_model='--accept-model-review' in sys.argv
    sys.argv=[a for a in sys.argv if a!='--accept-model-review']
    left,right=map(Path,sys.argv[1:3])
    manifests=[json.loads((p/'manifest.json').read_text()) for p in (left,right)]
    try:recorded=validate_comparison_manifests(*manifests)
    except ValueError as exc:raise SystemExit(str(exc)) from None
    content=None;result_kinds={}
    if len(sys.argv)>4:
        from scripts.worker_visual_gold import evaluate_content_gate
        gates=[evaluate_content_gate(json.loads((p/'results.json').read_text()),json.loads(Path(r).read_text()),run=p.name,
                                     accept_model_review=accept_model,manifest=m) for p,r,m in zip((left,right),sys.argv[3:5],manifests)]
        result_kinds={'reviewer_kinds':{'before':gates[0]['reviewer_kinds'],'after':gates[1]['reviewer_kinds']}}
        content=tuple({(g['file'],g['page'],g['round']):g['state']=='passed' for g in gate['pages']} for gate in gates)
    result=compare(json.loads((left/'results.json').read_text()),json.loads((right/'results.json').read_text()),content)
    print(json.dumps({**recorded,**result_kinds,**result},ensure_ascii=False,indent=2,default=list))
