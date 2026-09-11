"""Acceptance criteria are written before implementing the vision probe checker."""
import pytest
from scripts.check_worker_vision import assess_response


def response(payload, finish='stop'):
    import json
    return {'choices':[{'finish_reason':finish,'message':{'content':json.dumps(payload)}}]}


def valid_code():
    return {'title':'Correction','blocks':[{'kind':'code','lines':[
        'if (taille == capacite) {',
        '    int nouvelle = capacite * 2;',
        '    int *temp = realloc(arr, nouvelle * sizeof(*arr));',
        '    if (temp == NULL) {', '        free(arr);', '        return 1;', '    }',
        '    arr = temp;', '    capacite = nouvelle;', '}',
        'arr[taille] = valeur;', 'taille++;',
    ]}], 'unreadable':[]}


def test_full_code_must_be_in_one_block():
    assert assess_response('full_code',response(valid_code()))['passed']
    p=valid_code();p['blocks'].append({'kind':'code','lines':p['blocks'][0]['lines'][-2:]})
    p['blocks'][0]['lines']=p['blocks'][0]['lines'][:-2]
    assert not assess_response('full_code',response(p))['passed']


def test_truncated_output_cannot_pass_even_if_json_is_valid():
    assert not assess_response('full_code',response(valid_code(),'length'))['passed']


def test_thinking_must_not_enter_accepted_payload():
    r=response(valid_code());r['choices'][0]['message']['content']='<think>private</think>'+r['choices'][0]['message']['content']
    result=assess_response('full_code',r)
    assert result['passed']
    assert '<think>' not in str(result['parsed'])


def test_schema_errors_and_unreadable_are_not_passes():
    p=valid_code();p['unexpected']='extra'
    assert not assess_response('full_code',response(p))['passed']
    p=valid_code();p['unreadable']=['code tail']
    assert not assess_response('full_code',response(p))['passed']


def test_does_not_silently_decode_literal_backslash_n_in_code():
    p=valid_code();p['blocks'][0]['lines']=['\\n'.join(p['blocks'][0]['lines'])]
    assert not assess_response('full_code',response(p))['passed']


def test_formula_typo_is_a_failure():
    p={'title':'IEEE 754','blocks':[{'kind':'formula','lines':[r'(-1)^{S} \\times (1.M) \\times 2^{E-biais}']}], 'unreadable':[]}
    assert assess_response('formula',response(p))['passed']
    p['blocks'][0]['lines']=[r'(-1)^{S} \\times (1.M) \\times 2^{E-bais}']
    assert not assess_response('formula',response(p))['passed']


def test_checker_imports_when_script_directory_is_the_only_project_path(tmp_path):
    import subprocess,sys
    from pathlib import Path
    scripts=Path(__file__).resolve().parents[2]/'scripts'
    code=f"import sys; sys.path.insert(0, {str(scripts)!r}); from check_worker_vision import assess_response; print(assess_response('full_code', {{'choices':[{{'finish_reason':'stop','message':{{'content':'{{}}'}}}}]}})['passed'])"
    p=subprocess.run([sys.executable,'-c',code],cwd=tmp_path,capture_output=True,text=True)
    assert p.returncode==0,p.stderr


def test_columns_check_rejects_changed_c_string_literal():
    p={'title':'Le retour se vérifie, toujours','blocks':[
        {'kind':'code','lines':['int *tab = malloc(n * sizeof(int));','if (tab == NULL) {',
            'fprintf(stderr, "plus∈de∈memoire\\n");','return 1;','}',
            'for (int i = 0; i < n; i++) {','tab[i] = i * 10;','}','free(tab);']},
        {'kind':'text','lines':['Une allocation peut échouer. Le test coûte trois lignes.']},
    ],'unreadable':[]}
    assert not assess_response('columns',response(p))['passed']
    p['blocks'][0]['lines'][2]='fprintf(stderr, "plus de memoire\\n");'
    assert assess_response('columns',response(p))['passed']
