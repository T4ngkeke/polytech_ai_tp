"""The lab citation report; the database part runs in test_m4d_chat_pg.py."""
from backend.app.services.citation_report import EXCERPT_CHARS, _excerpt


def test_an_excerpt_is_plain_text_and_bounded():
    assert _excerpt('Les <sup>f</sup>onctions  de\ngroupe') == 'Les fonctions de groupe'
    assert _excerpt('<table><tr><td>AVG</td><td>moyenne</td></tr></table>') == 'AVG moyenne'
    long = _excerpt('mot ' * 100)
    assert len(long) == EXCERPT_CHARS + 1 and long.endswith('…')
    assert _excerpt(None) == ''
    assert _excerpt('\uf0a7 Dans la liste') == 'Dans la liste'
    assert _excerpt('```sql select count(\\*) from Vins; ```') == 'select count(*) from Vins;'
