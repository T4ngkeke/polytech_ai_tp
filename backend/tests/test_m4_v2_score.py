"""Facet scoring for benchmark v2 on synthetic trial rows."""
import json

import pytest

from scripts.m4_v2_score import QUESTIONS, main, score_trial

# The frozen benchmark questions are local files, not in git.
pytestmark = pytest.mark.skipif(not QUESTIONS.exists(), reason='frozen benchmark v2 questions not present')


def test_facet_coverage_and_precision_use_acceptable_and_related_pages():
    spec = json.loads(QUESTIONS.read_text())
    names = {'SQL': 'CoursInfoDeBase-2022_SQL.pdf', 'ALG': 'Algebre_relationnelle_2020.pdf',
             'A2S': 'De_LAlgebre_Relationnel_au_SQL_2022.pdf', 'GRA': 'Algorithme_Graphe_2022-2023.pdf'}
    n07 = next(q for q in spec['questions'] if q['question_id'] == 'N07')
    # N07: one course facet (SQL p.44) and one absent facet; SQL p.42 is related.
    row = {'cited_pages': [['CoursInfoDeBase-2022_SQL.pdf', 44], ['CoursInfoDeBase-2022_SQL.pdf', 42],
                           ['CoursInfoDeBase-2022_SQL.pdf', 5]], 'stop_reason': 'completed'}
    score = score_trial(row, n07, names)
    assert (score['covered'], score['total'], score['complete']) == (1, 1, True)
    assert score['cited_ok'] == 2 and round(score['precision'], 2) == 0.67
    empty = score_trial({'cited_pages': [], 'stop_reason': 'partial_error'}, n07, names)
    assert empty['precision'] is None and empty['partial'] and not empty['complete']


def test_report_files_are_written(tmp_path):
    rows = [{'variant': 'dsh_native', 'question_id': 'N22', 'round': 1, 'stop_reason': 'completed',
             'cited_pages': [['Algorithme_Graphe_2022-2023.pdf', 3]], 'answer': 'Bellman [E1]',
             'usage': [{'input_tokens': 10, 'output_tokens': 2, 'usage_source': 'actual'}],
             'elapsed_seconds': 4.0, 'tool_calls': 2},
            {'variant': 'dsh_native', 'question_id': 'GREETING_PROBE', 'round': 1, 'stop_reason': 'completed',
             'cited_pages': [], 'answer': 'Bonjour', 'usage': [], 'elapsed_seconds': 1.0, 'tool_calls': 0}]
    (tmp_path / 'results.json').write_text(json.dumps(rows))
    main(tmp_path)
    scores = json.loads((tmp_path / 'scores.json').read_text())
    assert scores['summary']['dsh_native']['facet_coverage'] == '2/2'
    assert scores['summary']['dsh_native']['complete_questions'] == 1
    assert scores['probes'][0]['question_id'] == 'GREETING_PROBE'
    assert 'N22' in (tmp_path / 'SIDE_BY_SIDE_V2.md').read_text()


def test_absent_only_question_is_never_complete():
    spec = json.loads(QUESTIONS.read_text())
    n27 = next(q for q in spec['questions'] if q['question_id'] == 'N27')
    score = score_trial({'cited_pages': [], 'stop_reason': 'completed'}, n27, {'GRA': 'Algorithme_Graphe_2022-2023.pdf'})
    assert score['total'] == 0 and not score['complete']
