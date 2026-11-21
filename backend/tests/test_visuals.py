"""test_visuals.py — diagrams and charts only in the two fixed formats."""
import json

from backend.app.agent import visuals as vz


def block(kind, body):
    return f'```{kind}\n{body}\n```'


def test_a_valid_mermaid_diagram_stays():
    answer = 'Voici les appels [E1] :\n\n' + block('mermaid', 'flowchart TD\n  A[fact 3] --> B[fact 2]') + '\n\nFin.'
    assert vz.checked(answer) == answer


def test_an_unknown_or_configured_mermaid_block_becomes_text():
    assert vz.checked(block('mermaid', 'evilDiagram\n  x')) == block('text', 'evilDiagram\n  x')
    configured = "%%{init: {'securityLevel': 'loose'}}%%\nflowchart TD\n  A --> B"
    assert vz.checked(block('mermaid', configured)) == block('text', configured)
    assert vz.mermaid_ok('%% a comment\nsequenceDiagram\n  A->>B: appel')


def test_a_valid_chart_is_kept_in_its_checked_form():
    chart = {'type': 'line', 'title': 'Temps du tri', 'x_label': 'n', 'y_label': 's',
             'labels': [10, 100, 1000], 'series': [{'name': 'bulles', 'values': [0.01, 0.4, 41]}]}
    out = vz.checked('Mesures :\n' + block('chart', json.dumps(chart, indent=2)))
    assert out.startswith('Mesures :\n```chart\n')
    kept = json.loads(out.split('\n')[2])
    assert kept['series'][0]['values'] == [0.01, 0.4, 41.0] and kept['labels'] == [10, 100, 1000]


def test_a_chart_that_does_not_fit_the_shape_becomes_plain_json():
    for wrong in [
        {'type': 'bar', 'labels': ['a', 'b'], 'series': [{'values': [1]}]},              # one value missing
        {'type': 'pie', 'labels': ['a'], 'series': [{'values': [-1]}]},                  # negative share
        {'type': 'scatter', 'labels': ['x'], 'series': [{'values': [1]}]},               # x not a number
        {'type': 'html', 'labels': ['a'], 'series': [{'values': [1]}]},                  # unknown type
        {'type': 'bar', 'labels': ['a'], 'series': [{'values': [1]}], 'onclick': 'x'}]:  # extra key
        source = json.dumps(wrong)
        assert vz.checked(block('chart', source)) == block('json', source)
    assert vz.checked(block('chart', 'not json')) == block('json', 'not json')


def test_other_code_blocks_and_plain_text_are_left_alone():
    answer = 'Code :\n' + block('python', 'print(1)') + '\nEt `inline`.'
    assert vz.checked(answer) == answer and vz.checked('Sans bloc.') == 'Sans bloc.'
