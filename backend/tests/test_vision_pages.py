"""Acceptance criteria for the full-course page sweep, written before the checker.

The three-page probe judged hand-picked pages against hand-written expectations.
A 142-page sweep has no such expectations, so the only available ground truth is
the PDF text layer. These tests pin how that comparison must behave.
"""
from scripts.check_worker_vision_pages import compare_page


def blocks(*pairs):
    return [{'kind': k, 'lines': l} for k, l in pairs]


SOURCE = (
    'Le retour se vérifie, toujours\n'
    'int *tab = malloc(n * sizeof(int ));\n'
    'fprintf(stderr , "plus␣de␣memoire\\n");\n'
    'free(tab );\n'
    '• Une allocation peut échouer\n'
    'Ce qu’on a déjà\n'
)


def test_faithful_transcription_reports_full_coverage_and_no_invention():
    result = compare_page(SOURCE, blocks(
        ('text', ['Le retour se vérifie, toujours', 'Une allocation peut échouer', "Ce qu'on a déjà"]),
        ('code', ['int *tab = malloc(n * sizeof(int));',
                  'fprintf(stderr, "plus␣de␣memoire\\n");', 'free(tab);']),
    ))
    assert result['missing'] == []
    assert result['invented'] == []
    assert result['foreign_symbols'] == []
    assert result['coverage'] == 1.0


def test_dropped_source_line_is_reported_as_missing():
    result = compare_page(SOURCE, blocks(('text', ['Le retour se vérifie, toujours'])))
    assert 'malloc' in result['missing']
    assert result['coverage'] < 1.0


def test_invented_mathematical_symbol_inside_code_is_flagged():
    # The exact ornith failure: the words survive, only the separator changes.
    result = compare_page(SOURCE, blocks(
        ('code', ['fprintf(stderr, "plus∈de∈memoire\\n");']),
    ))
    assert '∈' in result['foreign_symbols']


def test_visible_space_marker_may_be_written_as_a_plain_space():
    result = compare_page(SOURCE, blocks(
        ('code', ['fprintf(stderr, "plus de memoire\\n");']),
    ))
    assert result['foreign_symbols'] == []


def test_listing_spacing_and_typographic_quotes_are_not_differences():
    result = compare_page('free(tab );\nCe qu’on a déjà\n', blocks(
        ('code', ['free(tab);']), ('text', ["Ce qu'on a déjà"]),
    ))
    assert result['missing'] == []
    assert result['invented'] == []


def test_latex_written_for_a_formula_is_not_counted_as_invented_text():
    result = compare_page('Nombre = (−1)S × (1:M) × 2E−biais\n', blocks(
        ('formula', ['\\text{Nombre} = (-1)^S \\times (1.M) \\times 2^{E-\\text{biais}}']),
    ))
    assert result['invented'] == []
    assert result['foreign_symbols'] == []


def test_words_absent_from_the_page_are_reported_as_invented():
    result = compare_page('free(tab );\n', blocks(('text', ['Cette diapositive explique malloc'])))
    assert 'diapositive' in result['invented']


def test_empty_source_page_does_not_divide_by_zero():
    assert compare_page('   \n', blocks(('text', ['Titre'])))['coverage'] == 1.0


def test_title_text_counts_as_transcribed():
    # A title slide puts most of its words in the title field. Ignoring that
    # field reports the page as half missing when nothing was actually dropped.
    result = compare_page('CM1 - Introduction au langage C\n',
                          blocks(('text', ['Cours 1'])), title='CM1 - Introduction au langage C')
    assert result['missing'] == []
    assert result['coverage'] == 1.0


def test_subscript_and_superscript_digits_match_the_flat_text_layer():
    # The text layer flattens base markers: 111011 with a subscript 2 extracts
    # as "1110112". Writing the subscript is more faithful, not a difference.
    result = compare_page('1110112 = 5910\n', blocks(('text', ['111011₂ = 59₁₀'])))
    assert result['missing'] == []
    assert result['invented'] == []
    assert result['foreign_symbols'] == []


def test_dropped_source_lines_are_listed_line_by_line():
    from scripts.check_worker_vision_pages import dropped_lines
    source = 'Titre de la page\nPremière puce visible\nSeconde puce oubliée\n'
    assert dropped_lines(source, blocks(('text', ['Titre de la page', 'Première puce visible'])),
                         title='') == ['Seconde puce oubliée']


def test_latex_notation_does_not_make_a_line_look_dropped():
    from scripts.check_worker_vision_pages import dropped_lines
    source = 'Soit NB un nombre en base B sur n chiffres : NB = xn−1 : : : x2x1x0\n'
    assert dropped_lines(source, blocks(
        ('text', ['Soit N_B un nombre en base B sur n chiffres : N_B = x_{n-1} ... x_2 x_1 x_0'])), '') == []


def test_page_number_and_short_lines_are_not_counted_as_dropped():
    from scripts.check_worker_vision_pages import dropped_lines
    assert dropped_lines('Titre\n18\n', blocks(('text', ['Titre'])), '') == []
