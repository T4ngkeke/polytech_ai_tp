"""What search reads of a PDF (KNOWN_ISSUES 5, 7, 8): a table cut between its
rows and searched as plain text, the captions and notes of pictures and
tables kept as text, and a header or footer repeated on many pages left out
of search. No MinerU and no model: pdf_fixtures stands in."""
import hashlib
import uuid

import pytest
from sqlalchemy import select

from backend.app.models import DocChunk, DocumentBlock, DocumentRevision, EMBEDDING_DIM
from backend.app.services import document_edits
from backend.tests import pdf_fixtures
from backend.tests.test_structured_ingest import run_job, storage, uploaded  # noqa: F401 - fixture
from backend.worker import structured_index
from backend.worker.pdf_reader import read_pdf
from backend.worker.table_chunks import row_texts, split_table

ROWS = ''.join(f'<tr><td>conv{n}</td><td>{n}&#x27;</td></tr>' for n in range(1, 9))
TABLE = f'<table><tr><td>Layer</td><td>Size</td></tr>{ROWS}</table>'


# --- tables ----------------------------------------------------------------

def test_a_table_is_cut_between_rows_and_each_part_has_the_header():
    parts = split_table(TABLE, target_bytes=40)
    assert len(parts) > 2
    assert ''.join(p.content for p in parts) == TABLE
    assert all(p.content == TABLE[p.start:p.end] for p in parts)
    assert parts[0].content.startswith('<table><tr>') and parts[-1].content.endswith('</tr></table>')
    for part in parts[1:]:
        assert part.content.startswith('<tr>') and part.search_text.splitlines()[0] == 'Layer | Size'
    assert all(p.content.endswith('</tr>') for p in parts[:-1])
    assert parts[0].search_text.splitlines()[:2] == ['Layer | Size', "conv1 | 1'"]
    assert all('<' not in p.search_text and '&#x27;' not in p.search_text for p in parts)


def test_merged_cells_are_repeated_in_the_search_text():
    html = ('<table><tr><td rowspan="2">CNN</td><td colspan="2">Validation</td></tr>'
            '<tr><td>top-1</td><td>top-5</td></tr><tr><td>VGG</td><td>0.7<br>1</td><td>$x^{2}$</td></tr></table>')
    assert row_texts(html) == ['CNN | Validation | Validation', 'CNN | top-1 | top-5', 'VGG | 0.7 1 | $x^{2}$']


@pytest.mark.parametrize('content', ['plain text', '| a | b |\n|---|---|\n| 1 | 2 |',
                                     '<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>',
                                     '<table></table>'])
def test_what_is_not_one_html_table_is_cut_as_text(content):
    assert split_table(content) is None


def test_the_index_reads_a_table_part_by_its_search_text():
    block = uuid.uuid4()
    snapshot = [(block, TABLE, TABLE, {}, [], 'searchable', False, 1, None, 'none', 'table'),
                (uuid.uuid4(), 'Du texte.', 'Du texte.', {}, [], 'searchable', False, 1, None, 'none', 'text')]
    prepared = structured_index._prepare(snapshot)
    tables = [i for i in prepared if i[0] == block]
    assert ''.join(i[3].content for i in tables) == TABLE
    assert all(structured_index.search_text(i).startswith('Layer | Size') for i in tables)
    text = [i for i in prepared if i[0] != block]
    assert text[0][10] is None and structured_index.search_text(text[0]) == 'Du texte.'


@pytest.mark.asyncio
async def test_a_stored_table_part_keeps_its_range_and_searches_its_text(db_session, storage, tmp_path):
    teacher, lab, body = await uploaded(db_session, storage, pdf_fixtures.course_pdf(1))

    def extract(path, number, output):
        return [{'type': 'table', 'table_body': TABLE, 'bbox': [80, 100, 900, 800], 'page_idx': 0}]
    assert await run_job(db_session, storage, tmp_path, extractor=extract)
    embedded = []

    async def embed(texts):
        embedded.extend(texts)
        return [[0.0] * (EMBEDDING_DIM - 1) + [1.0] for _ in texts]
    assert await structured_index.process_index_job(db_session, embed_fn=embed)
    rev = uuid.UUID(body['revision_id'])
    chunks = (await db_session.scalars(select(DocChunk).where(DocChunk.revision_id == rev)
                                       .order_by(DocChunk.chunk_index))).all()
    assert ''.join(c.content for c in chunks) == TABLE
    assert all(c.context and c.context.startswith('Layer | Size') for c in chunks)
    assert embedded == [c.context for c in chunks]
    revision = await db_session.get(DocumentRevision, rev)
    assert revision.index_ready


# --- captions and notes -------------------------------------------------------

def test_captions_and_notes_become_text_blocks_around_their_block(tmp_path):
    path = tmp_path / 'c.pdf'
    path.write_bytes(pdf_fixtures.course_pdf(1))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    def items(*_):
        return [
            {'type': 'table', 'table_body': '<table><tr><td>a</td></tr></table>', 'bbox': [80, 100, 900, 300],
             'table_caption': ['Table: Total learnable parameters'], 'table_footnote': ['Source: test'], 'page_idx': 0},
            {'type': 'image', 'bbox': [80, 400, 900, 700], 'image_caption': ['118 telescopes, 210 000 TB', ' '],
             'image_footnote': ['\x00note'], 'page_idx': 0},
        ]
    doc, _ = read_pdf(path, digest, tmp_path / 'work', items)
    page = doc.pages[0]
    assert [(b.kind, b.content) for b in page.blocks if b.kind != 'figure'] == [
        ('text', 'Table: Total learnable parameters'), ('table', '<table><tr><td>a</td></tr></table>'),
        ('text', 'Source: test'), ('text', '118 telescopes, 210 000 TB'), ('text', 'note')]
    assert [b.kind for b in page.blocks] == ['text', 'table', 'text', 'figure', 'text', 'text']
    assert [b.order_index for b in page.blocks] == list(range(6))
    assert len({b.block_id for b in page.blocks}) == 6
    note = page.blocks[0]
    assert note.source_ref.content_origin == 'mineru_model' and note.source_text == note.content
    assert note.source_ref.legacy_ref['field'] == 'table_caption' and page.state == 'usable'


# --- repeated headers and footers --------------------------------------------

@pytest.mark.asyncio
async def test_a_header_on_three_pages_or_more_is_left_out_of_search(db_session, storage, tmp_path):
    await uploaded(db_session, storage, pdf_fixtures.course_pdf(4))

    def extract(path, number, output):
        items = [{'type': 'text', 'text': f'Texte {number}', 'bbox': [80, 200, 900, 300], 'page_idx': 0},
                 {'type': 'header', 'text': 'Deep  Learning', 'bbox': [0, 0, 1000, 50], 'page_idx': 0},
                 {'type': 'page_number', 'text': f'{number}/4', 'bbox': [900, 950, 1000, 990], 'page_idx': 0}]
        if number <= 2:
            items.append({'type': 'footer', 'text': 'Chapitre rare', 'bbox': [0, 950, 500, 990], 'page_idx': 0})
        if number == 4:
            items[1]['text'] = 'deep learning'
        return items
    assert await run_job(db_session, storage, tmp_path, extractor=extract)
    blocks = (await db_session.scalars(select(DocumentBlock))).all()
    state = {}
    for b in blocks:
        state.setdefault((b.content, b.index_state), 0)
        state[(b.content, b.index_state)] += 1
    assert state[('Deep  Learning', 'excluded')] == 3 and state[('deep learning', 'excluded')] == 1
    assert state[('Chapitre rare', 'searchable')] == 2
    assert state[('1/4', 'searchable')] == 1 and state[('Texte 1', 'searchable')] == 1
    header = next(b for b in blocks if b.content == 'deep learning')
    assert header.structure['exclusion'] == {'by': None, 'at': None, 'reason': 'repeated_navigation', 'pages': 4}
    # A new reading decides it again: it is not a teacher's change to carry over.
    assert not document_edits._teacher_changed(header)
    header.structure = {**header.structure, 'exclusion': {'by': str(uuid.uuid4()), 'at': 'now', 'reason': None}}
    assert document_edits._teacher_changed(header)


def test_the_model_reads_a_table_lead_as_plain_text():
    from backend.app.agent.tools.course import _snippet_text
    assert _snippet_text({'content': '<tr><td>conv6</td></tr>', 'search_text': 'Layer | Size\nconv6 | 6'}) \
        == 'Layer | Size\nconv6 | 6'
    assert _snippet_text({'content': 'Du texte.'}) == 'Du texte.'
