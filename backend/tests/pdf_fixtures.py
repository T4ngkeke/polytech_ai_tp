"""PDF test documents and a stand-in for MinerU, for the structured-document tests.

course_pdf builds a small real PDF (pymupdf). page_items is what MinerU would
return for one of its pages: a heading and a paragraph. extractor wraps it for
StructuredSettings jobs and can run a coroutine on the test loop while the
worker is inside its no-transaction step, which is when a teacher could act.
"""
import asyncio

import pymupdf


def course_pdf(pages: int = 3, *, title: str = 'Cours') -> bytes:
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((70, 80), f'{title} {number}', fontsize=15)
        page.insert_text((70, 130), f'Texte de la page {number}')
    data = doc.tobytes()
    doc.close()
    return data


CODE = 'SELECT\tnom,   prix\nFROM\tproduit\nWHERE prix > 10'
TABLE = '<table><tr><td>nom</td><td>prix</td></tr></table>'


def page_items(_path, number, _output):
    """MinerU's content list for one page: a heading and a paragraph; page 1
    also has a code block, a table and a figure (linked to the page image)."""
    items = [
        {'type': 'text', 'text': f'Titre {number}', 'text_level': 1, 'bbox': [80, 60, 900, 140], 'page_idx': 0},
        {'type': 'text', 'text': f'Texte de la page {number}', 'bbox': [80, 180, 900, 300], 'page_idx': 0},
    ]
    if number == 1:
        items += [{'type': 'code', 'code_body': CODE, 'bbox': [80, 320, 900, 420], 'page_idx': 0},
                  {'type': 'table', 'table_body': TABLE, 'bbox': [80, 440, 900, 560], 'page_idx': 0},
                  {'type': 'image', 'bbox': [80, 600, 900, 900], 'page_idx': 0}]
    return items


def extractor(*, side_effect=None, loop=None, fail_pages=()):
    """page_items, running side_effect once (on the first page) when given.
    A page in fail_pages fails as MinerU can: the reader keeps it as partial."""
    done = []

    def extract(path, number, output):
        if side_effect is not None and not done:
            done.append(True)
            asyncio.run_coroutine_threadsafe(side_effect(), loop).result(10)
        if number in fail_pages:
            raise RuntimeError('MinerU stand-in: page failed')
        return page_items(path, number, output)
    return extract
