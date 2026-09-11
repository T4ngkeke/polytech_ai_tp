from scripts.compare_worker_layout import compare


def row(page,ok,calls):
    trace=[{'seconds':1}]*calls
    audit={'pages':[{'trace':trace}] if ok else [],'page_results':[{'trace':trace}]}
    return {'file':'x.pdf','page':page,'round':1,'status':'accepted' if ok else 'needs_review','seconds':calls,'audit':audit}


def test_call_saving_cannot_hide_new_failed_pages():
    result=compare([row(1,True,4),row(2,True,4)],[row(1,True,2),row(2,False,1)])
    assert result['common_success_mean_calls']=={'before':4,'after':2}
    assert result['after_total_calls']==3
    assert not result['mechanical_gate_passed']


def test_native_formula_coverage_is_not_visual_formula_success():
    from scripts.worker_visual_gold import check_page
    r={'file':'main-2.pdf','page':55,'audit':{'pages':[{'groups':[
        {'kind':'text','text':'Nombre = (−1)S × (1:M) × 2E−biais','visual':None}]}]}}
    assert check_page(r)['formula_structure_preserved'] is False


def test_uml_page_needs_a_figure_description_and_its_printed_class_names():
    from scripts.worker_visual_gold import check_page
    names='Employee\nManager\nProgrammer'
    r={'file':'Heritage.pdf','page':8,'audit':{'pages':[{'groups':[{'kind':'text','text':names,'visual':None}]}]}}
    assert check_page(r)['figure_described'] is False
    r['audit']['pages'][0]['groups'][0]={'kind':'figure','text':'Hiérarchie de classes.\n'+names,
        'visual':{'kind':'figure','description':'Hiérarchie de classes.'}}
    assert check_page(r)=={'figure_described':True,'class_names_kept':True}


def test_partial_pages_are_not_counted_as_automatic_acceptance():
    from scripts.check_worker_layout import aggregate
    rows=[{'status':'partial','seconds':1,'audit':{'reviews':[{'reason':'unclear'}],
        'pages':[{'trace':[{'error':'same'}]}],'page_results':[{'reason':None}]}},
        {'status':'accepted','seconds':1,'audit':{'pages':[{'trace':[{'error':'same'}]}]}}]
    summary=aggregate(rows)
    assert summary['accepted']==1
    assert summary['partial']==1
    assert summary['review_tasks']==1
    assert summary['repeated_rejections']==0


# --- batch 4: resume vs. comparison, human content gate, fixed samples -------

import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / 'backend/tests/fixtures/worker_visual_cases.json').read_text())


def manifest(**change):
    base = {'cases': [['main-2.pdf', 55]], 'files': {'main-2.pdf': 'a' * 64}, 'rounds': 2,
            'code': {'backend/worker/x.py': '1'}, 'script_sha256': 's1', 'git_commit': 'c1',
            'harness': 'region', 'group_correction': True, 'frequency_penalty': None,
            'model': 'ornith', 'base_url': 'http://127.0.0.1:8001/v1', 'temperature': 0,
            'max_tokens': 8192, 'timeout': 120, 'workers': 1,
            'model_service': {'id': 'ornith', 'root': '/model', 'max_model_len': 32768},
            'model_weights': 'Ornith-1.5-35B-A3B-NVFP4 config sha256 x'}
    return {**base, **change}


def test_resume_needs_identical_code_prompt_options_and_files():
    from scripts.check_worker_layout import validate_resume_manifest
    validate_resume_manifest(manifest(), manifest())
    for change in ({'code': {'backend/worker/x.py': '2'}}, {'script_sha256': 's2'}, {'files': {'main-2.pdf': 'b' * 64}},
                   {'temperature': 0.2}, {'harness': 'v4'}):
        with pytest.raises(ValueError, match=next(iter(change))):
            validate_resume_manifest(manifest(), manifest(**change))


def test_comparison_allows_code_changes_but_not_sample_or_model_changes():
    from scripts.compare_worker_layout import validate_comparison_manifests
    old = manifest(harness='v4', code={'backend/worker/x.py': '0'}, script_sha256='s0', git_commit='e822b35')
    recorded = validate_comparison_manifests(old, manifest())
    assert recorded['code_changed'] and recorded['versions'] == {'before': 'e822b35', 'after': 'c1'}
    assert recorded['limitations'] == []
    for change in ({'cases': [['main-2.pdf', 6]]}, {'rounds': 1}, {'files': {'main-2.pdf': 'b' * 64}},
                   {'model': 'other'}, {'temperature': 0.5}, {'max_tokens': 4096}, {'frequency_penalty': 0.3},
                   {'model_service': {'id': 'ornith', 'root': '/other', 'max_model_len': 32768}}):
        with pytest.raises(ValueError, match=next(iter(change))):
            validate_comparison_manifests(old, manifest(**change))
    with pytest.raises(ValueError, match='code'):
        validate_comparison_manifests({k: v for k, v in old.items() if k != 'code'}, manifest())
    legacy = {k: v for k, v in old.items() if k not in ('git_commit', 'model_service', 'model_weights')}
    limits = validate_comparison_manifests(legacy, manifest())['limitations']
    assert any('model' in item for item in limits) and any('commit' in item for item in limits)


def audited(page=55, text='(−1)^S', reviews=()):
    return {'file': 'main-2.pdf', 'page': page, 'round': 1, 'status': 'accepted', 'seconds': 1,
            'source_file_sha256': CASES['files']['main-2.pdf']['sha256'],
            'audit': {'pages': [{'groups': [{'kind': 'formula', 'ids': ['p55l1'], 'text': text}], 'trace': [{}]}],
                      'reviews': list(reviews)}}


def record(row, run='run-1', human=True, **results):
    from scripts.worker_visual_gold import output_digest
    case = next(c for c in CASES['focus'] if (c['file'], c['page']) == (row['file'], row['page']))
    values = {c['id']: 'correct' for c in case['checks']}
    values.update(results)
    return {'run': run, 'file': row['file'], 'page': row['page'], 'round': row['round'],
            'source_sha256': row['source_file_sha256'], 'output_sha256': output_digest(row),
            'reviewer': 'teacher', 'human': human, 'results': values, 'invented_facts': [], 'unnecessary_reviews': 0}


def test_content_gate_needs_a_current_human_record():
    from scripts.worker_visual_gold import evaluate_content_gate
    row = audited()
    assert evaluate_content_gate([row], [], run='run-1')['pages'][0]['state'] == 'pending'
    assert evaluate_content_gate([row], [record(row, human=False)], run='run-1')['pages'][0]['state'] == 'pending'
    assert evaluate_content_gate([row], [record(row, run='run-0')], run='run-1')['pages'][0]['state'] == 'pending'
    stale = record(row)
    changed = audited(text='(−1)^S changed')
    gate = evaluate_content_gate([changed], [stale], run='run-1')
    assert gate['pages'][0]['state'] == 'stale' and not gate['passed']
    gate = evaluate_content_gate([row], [record(row)], run='run-1')
    assert gate['passed'] and gate['pages'][0]['state'] == 'passed'


def test_content_gate_counts_errors_omissions_and_reviews_honestly():
    from scripts.worker_visual_gold import evaluate_content_gate
    row = audited()
    reviewed = evaluate_content_gate([row], [record(row, power_of_two='reviewed')], run='run-1')
    assert reviewed['passed'] and reviewed['sent_to_review'] == 1
    wrong = evaluate_content_gate([row], [record(row, sign_exponent='wrong')], run='run-1')
    assert not wrong['passed'] and wrong['important_errors'] == 1
    missing = evaluate_content_gate([row], [record(row, bit_widths='missing')], run='run-1')
    assert not missing['passed'] and missing['silent_omissions'] == 1
    invented = record(row); invented['invented_facts'] = ['says double precision']
    assert not evaluate_content_gate([row], [invented], run='run-1')['passed']
    unchecked = record(row); del unchecked['results']['bias_127']
    assert evaluate_content_gate([row], [unchecked], run='run-1')['pages'][0]['state'] == 'incomplete'
    code = {**row, 'file': 'main-3.pdf', 'page': 54, 'source_file_sha256': CASES['files']['main-3.pdf']['sha256']}
    # Program-copied code cannot be passed by sending it to review.
    assert not evaluate_content_gate([code], [record(code, code_block='reviewed')], run='run-1')['passed']


def test_checklist_is_prepared_but_never_marked_as_human():
    from scripts.worker_visual_gold import checklist, evaluate_content_gate
    row = audited()
    items = checklist([row], run='run-1')
    assert items[0]['human'] is False and set(items[0]['results'].values()) == {None}
    assert {c['id'] for c in items[0]['checks']} == set(items[0]['results'])
    assert evaluate_content_gate([row], items, run='run-1')['pages'][0]['state'] == 'pending'


def test_efficiency_counts_only_pages_correct_in_both_runs():
    from scripts.compare_worker_layout import compare
    before = [row(1, True, 4), row(2, True, 4)]
    after = [row(1, True, 2), row(2, True, 2)]
    gates = ({('x.pdf', 1, 1): True, ('x.pdf', 2, 1): True}, {('x.pdf', 1, 1): True, ('x.pdf', 2, 1): False})
    result = compare(before, after, content=gates)
    assert result['common_success_pages'] == [('x.pdf', 1, 1)]
    assert result['efficiency_target_met'] is True
    same = compare(before, [row(1, True, 4), row(2, True, 4)], content=(gates[0], gates[0]))
    assert same['efficiency_target_met'] is False
    assert compare(before, after)['efficiency_target_met'] is None


def test_aggregate_separates_review_reasons_and_failures():
    from scripts.check_worker_layout import aggregate
    rows = [{'status': 'partial', 'seconds': 1, 'audit': {'pages': [{'trace': [{}]}], 'page_results': [{'reason': None}],
             'reviews': [{'reason_code': 'content_review'}, {'reason_code': 'render_failure'}]}},
            {'status': 'needs_review', 'seconds': 1, 'audit': {'pages': [{'trace': [{}]}],
             'page_results': [{'reason': 'model_request_failure'}], 'reviews': [{'reason_code': 'protocol_failure'}]}}]
    summary = aggregate(rows)
    assert summary['review_reasons'] == {'content_review': 1, 'render_failure': 1, 'protocol_failure': 1}
    assert summary['page_failure_reasons'] == {'model_request_failure': 1}


def test_fixed_samples_match_files_and_are_not_read_by_the_worker():
    import hashlib
    assert len(CASES['focus']) == 8 and all(c['checks'] for c in CASES['focus'])
    assert {(a['file'], a['page']) for a in CASES['source_anomalies']} == {('main-2.pdf', 58), ('main-2.pdf', 65), ('main-2.pdf', 68)}
    assert sum(f['pages'] for f in CASES['files'].values()) == 164
    for name, info in CASES['files'].items():
        path = ROOT / 'cours' / name
        if path.exists():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == info['sha256']
    for source in (ROOT / 'backend').rglob('*.py'):
        if 'tests' not in source.parts:
            assert 'worker_visual_cases' not in source.read_text(), source
