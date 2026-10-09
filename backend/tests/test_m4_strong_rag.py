from scripts.m4_evaluate import rag_candidates, rag_page_text


def index():
    return {'a': {'filename': 'a.pdf', 'pages': [{'page_id': f'a{n}', 'pdf_page_no': n} for n in (1, 2, 3, 4)]},
            'b': {'filename': 'b.pdf', 'pages': [{'page_id': f'b{n}', 'pdf_page_no': n} for n in (1, 2)]}}


def test_found_pages_first_then_adjacent_pages_without_duplicates():
    items = [{'page_id': 'a2'}, {'page_id': 'b1'}, {'page_id': 'a2'}, {'page_id': 'a3'}]
    candidates, where = rag_candidates(items, index())
    assert candidates == [('a2', 'found'), ('b1', 'found'), ('a3', 'found'),
                          ('a1', 'adjacent'), ('b2', 'adjacent'), ('a4', 'adjacent')]
    assert where['b2'] == ('b', 1)


def test_page_text_skips_page_numbers_and_keeps_descriptions():
    result = {'items': [{'type': 'navigation', 'content': '12'},
                        {'type': 'navigation', 'content': 'Sommaire : arbres'},
                        {'type': 'figure', 'content': 'Fig. 1', 'content_description': 'un arbre binaire'}]}
    text = rag_page_text(result)
    assert '12' not in text.split('\n')
    assert 'Sommaire : arbres' in text and 'un arbre binaire' in text
    assert rag_page_text({**result, 'next_cursor': 'x'}).endswith('(page text truncated)')
