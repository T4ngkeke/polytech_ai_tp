"""Behaviour of the native ODP reader. No database, no model, no network.

The checks here are about what the reader must not lose and what it must not
claim: exact whitespace, every cell of a table, every picture inside an object,
no hidden slide, no notes text, no invented LaTeX, and a refusal on packages
that are not safe to open.

A fixture passing is a protocol result. It says nothing about quality on the
real courseware, which is checked separately by a person.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from backend.tests import odp_fixtures
from backend.worker.document_ir import IRError, ParsedBlock, SourceRef
from backend.worker.odf_package import OdfLimits, OdfPackageError, open_package
from backend.worker.odp_reader import (
    IDENTITY_VERSION,
    OdpReadError,
    ReaderOptions,
    read_odp,
)


@pytest.fixture
def deck(tmp_path):
    return odp_fixtures.write_course_deck(tmp_path / 'cours.odp')


@pytest.fixture
def parsed(deck):
    return read_odp(deck)


def _block_named(document, draw_name):
    for _, block in document.all_blocks():
        if draw_name in block.source_ref.native_draw_names:
            return block
    raise AssertionError(f'no block from {draw_name!r}')


def _blocks_of(document, slide_no):
    page = next(page for page in document.pages if page.source_slide_no == slide_no)
    return page.blocks


# ---------------------------------------------------------------------------
# Source text
# ---------------------------------------------------------------------------

def test_code_whitespace_survives_exactly(parsed):
    code = _block_named(parsed, 'code')
    assert code.content == (
        'SELECT\tnom,   prix\n'
        'FROM\tproduit\nWHERE prix > 10;\n'
        '    indentation gardee'
    )


def test_no_block_content_is_stripped(parsed):
    code = _block_named(parsed, 'code')
    assert code.content.splitlines()[-1].startswith('    ')


def test_a_title_placeholder_becomes_a_heading(parsed):
    heading = _blocks_of(parsed, 1)[0]
    assert heading.kind == 'heading'
    assert heading.content == 'Algebre relationnelle'
    assert next(page for page in parsed.pages if page.source_slide_no == 1).title == 'Algebre relationnelle'


def test_positioned_text_is_flagged_without_guessing_math_or_touching_other_blocks(tmp_path, deck):
    with zipfile.ZipFile(deck) as archive:
        entries = {name: archive.read(name) for name in archive.namelist() if name != 'mimetype'}
    content = entries['content.xml'].decode()
    styles = (
        '<style:style style:name="RaisedBase" style:family="text">'
        '<style:text-properties style:text-position="33% 58%"/></style:style>'
        '<style:style style:name="Raised" style:family="text" '
        'style:parent-style-name="RaisedBase"/>'
        '<style:style style:name="Baseline" style:family="text">'
        '<style:text-properties style:text-position="0% 100%"/></style:style>'
    )
    content = content.replace('</office:automatic-styles>', styles + '</office:automatic-styles>', 1)
    content = content.replace(
        '<text:p>Algebre relationnelle</text:p>',
        '<text:p>Normal <text:span text:style-name="Baseline">T</text:span> '
        'M<text:span text:style-name="Raised">2</text:span></text:p>', 1)
    entries['content.xml'] = content
    styled = odp_fixtures._write(tmp_path / 'positioned.odp', entries)
    document = read_odp(styled)
    heading = _block_named(document, 'titre')
    assert heading.content == 'Normal T M2'
    assert heading.source_completeness == 'incomplete'
    assert [(issue.code, issue.detail.get('known_limitation'), issue.detail.get('style_names'))
            for issue in heading.issues] == [
                ('content_review', 'positioned_text_flattened', ['Raised'])]
    assert _block_named(document, 'code').source_completeness == 'complete'
    from backend.worker.structured_ingest import _index_state
    assert _index_state(heading) == 'needs_review'
    assert _index_state(_block_named(document, 'code')) == 'searchable'
    assert read_odp(styled).block_by_id(heading.block_id).issues[0].detail == heading.issues[0].detail


# ---------------------------------------------------------------------------
# Hidden slides, notes and their assets
# ---------------------------------------------------------------------------

def test_hidden_slide_is_found_through_the_style_chain(parsed):
    assert parsed.hidden_slide_numbers == (2,)
    assert parsed.visible_slide_numbers == (1, 3, 4)
    assert parsed.source_slide_count == 4


def test_the_hidden_slide_has_no_page_and_no_asset(parsed):
    assert all(page.source_slide_no != 2 for page in parsed.pages)
    entries = [asset.source_entry for asset in parsed.assets]
    assert 'Pictures/hidden.png' not in entries


def test_notes_text_is_never_carried_anywhere(parsed):
    payload = json.dumps(parsed.to_dict(), ensure_ascii=False)
    assert 'Notes secretes' not in payload
    assert 'Notes visibles' not in payload
    # The count is kept, the words are not: slide 2 and slide 4 carry notes.
    assert parsed.notes_slide_count == 2
    assert next(page for page in parsed.pages if page.source_slide_no == 4).notes_present is True


def test_hidden_slide_body_text_is_not_in_the_result(parsed):
    payload = json.dumps(parsed.to_dict(), ensure_ascii=False)
    assert 'Page masquee' not in payload


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def _cells(table_data, row_index):
    return [cell['text'] for cell in table_data['rows'][row_index]['cells']]


def test_repeated_cells_are_expanded_not_capped(parsed):
    table = _block_named(parsed, 'grille')
    assert _cells(table.table_data, 0)[:5] == ['X'] * 5
    assert table.source_completeness == 'complete'


def test_every_cell_carries_its_own_coordinates(parsed):
    table = _block_named(parsed, 'grille')
    first = table.table_data['rows'][0]['cells']
    assert [cell['column'] for cell in first] == list(range(len(first)))
    assert {cell['row'] for cell in first} == {0}
    assert table.table_data['rows'][1]['row_index'] == 1


def test_merged_cells_keep_their_span_and_their_covered_neighbour(parsed):
    table = _block_named(parsed, 'grille')
    merged = next(cell for cell in table.table_data['rows'][0]['cells']
                  if cell['text'] == 'entete fusionnee')
    assert merged['columns_spanned'] == 2
    covered = table.table_data['rows'][0]['cells'][-1]
    assert covered['covered'] is True
    # The covered cell keeps the column the merge actually reaches.
    assert covered['column'] == merged['column'] + 1
    assert table.table_data['merged_cells'] == 1
    assert table.table_data['covered_cells'] == 1


def test_embedded_spreadsheet_is_read_with_its_padding_reported(parsed):
    sheet = _block_named(parsed, 'feuille')
    assert sheet.kind == 'table'
    assert _cells(sheet.table_data, 0) == ['id', 'col', 'col', 'col']
    assert _cells(sheet.table_data, 1) == ['1', 'fusion', '', 'fin']
    # The 900 empty repeated cells at the end of the row, and the 500 empty
    # repeated rows after the last row with content, are padding. They are left
    # out and counted, and no cell that stays changes its column.
    assert sheet.table_data['dropped_trailing_empty_columns'] >= 900
    assert sheet.table_data['dropped_trailing_empty_rows'] >= 500
    assert sheet.source_completeness == 'complete'


def test_empty_rows_between_content_rows_keep_their_place(tmp_path):
    # Two repeated empty rows, then a row with content. The content row is on
    # row 2: collapsing the empty rows would move it up to row 0.
    path = odp_fixtures.write_table_with_empty_rows(tmp_path / 'trous.odp')
    document = read_odp(path)
    table = document.pages[0].blocks[0]
    rows = table.table_data['rows']
    assert [row['row_index'] for row in rows] == [0, 1, 2, 3]
    assert _cells(table.table_data, 0) == ['entete']
    assert rows[1]['cells'] == [] or all(not cell['text'] for cell in rows[1]['cells'])
    assert rows[2]['cells'] == [] or all(not cell['text'] for cell in rows[2]['cells'])
    assert _cells(table.table_data, 3) == ['apres le trou']
    assert table.table_data['row_count'] == 4
    assert table.source_completeness == 'complete'


def test_the_replacement_picture_does_not_replace_the_object(parsed):
    # A frame holds both the sheet and the picture the office suite drew for it.
    # Reading the picture first is what loses the table; this is the regression.
    sheet = _block_named(parsed, 'feuille')
    assert sheet.kind == 'table'
    assert sheet.table_data is not None
    replacement = [parsed.asset_by_id(asset_id) for asset_id in sheet.asset_ids]
    assert [asset.source_entry for asset in replacement] == ['ObjectReplacements/Object 2']
    assert replacement[0].notes == ('replacement picture of an embedded object, not the object itself',)


def test_a_replacement_that_is_not_a_picture_is_not_stored_as_one(parsed):
    drawing = _block_named(parsed, 'dessin')
    stored = [parsed.asset_by_id(asset_id).source_entry for asset_id in drawing.asset_ids]
    assert 'ObjectReplacements/Object 4' not in stored
    assert any('not a picture we can show' in issue.message for issue in drawing.issues)
    assert drawing.preview_required is True


def test_a_table_over_the_budget_is_reported_never_cut_in_silence(tmp_path):
    path = odp_fixtures.write_wide_table(tmp_path / 'large.odp', repeat=300)
    document = read_odp(path, options=ReaderOptions(max_table_cells=50))
    table = document.pages[0].blocks[0]
    assert table.table_data['truncated'] is True
    assert table.table_data['declared_cells'] == 300
    assert table.source_completeness == 'incomplete'
    codes = [issue.code for issue in table.issues]
    assert 'extraction_failure' in codes
    assert '300' in table.issues[0].message


def test_the_same_table_inside_the_budget_keeps_every_cell(tmp_path):
    path = odp_fixtures.write_wide_table(tmp_path / 'large.odp', repeat=300)
    document = read_odp(path)
    table = document.pages[0].blocks[0]
    assert len(table.table_data['rows'][0]['cells']) == 300
    assert table.table_data['truncated'] is False


def test_a_huge_repeat_is_refused_before_it_is_built(tmp_path):
    # The file asks for a hundred million cells. Reading it must not try.
    path = odp_fixtures.write_wide_table(tmp_path / 'bombe.odp', repeat=100_000_000)
    document = read_odp(path)
    table = document.pages[0].blocks[0]
    assert table.table_data['truncated'] is True
    assert table.table_data['read_cells'] == 0
    assert table.table_data['declared_cells'] == 100_000_000
    assert 'cell bound' in table.table_data['truncation_reason']
    assert table.source_completeness == 'incomplete'


def test_a_huge_row_repeat_is_refused_before_it_is_built(tmp_path):
    path = odp_fixtures.write_table_with_repeated_rows(tmp_path / 'lignes.odp', repeat=50_000_000)
    document = read_odp(path, options=ReaderOptions(max_table_rows=100))
    table = document.pages[0].blocks[0]
    assert table.table_data['truncated'] is True
    assert table.table_data['row_count'] <= 100
    assert 'row bound' in table.table_data['truncation_reason']
    assert table.source_completeness == 'incomplete'


# ---------------------------------------------------------------------------
# Pictures, groups and drawings
# ---------------------------------------------------------------------------

def test_every_picture_of_a_group_keeps_its_own_block_and_asset(parsed):
    figures = [block for block in _blocks_of(parsed, 3)
               if block.kind == 'figure' and block.asset_ids and block.group_path]
    entries = sorted(parsed.asset_by_id(block.asset_ids[0]).source_entry for block in figures)
    assert entries == ['Pictures/img1.png', 'Pictures/img2.png']
    assert len({parsed.asset_by_id(block.asset_ids[0]).sha256 for block in figures}) == 2


def test_a_label_inside_a_group_stays_native_text(parsed):
    label = _block_named(parsed, 'etiquette')
    assert label.kind == 'text'
    assert label.content == 'Sommet initial'
    assert label.group_path == ('schema',)


def test_plain_drawing_shapes_are_kept_together_and_ask_for_a_preview(parsed):
    connector = _block_named(parsed, 'fleche')
    assert connector.kind == 'figure'
    assert connector.preview_required is True
    # The preview target is the group, so the drawing is rendered whole.
    assert connector.preview_object_id.endswith('draw:g[1]')
    assert connector.preview_scope == 'object'
    assert any(issue.code == 'content_review' for issue in connector.issues)


def test_loose_drawing_shapes_ask_for_the_whole_page(tmp_path):
    # Three shapes side by side on the slide, held by no group. There is no one
    # object to render, so the block asks for the page instead of pointing at
    # the first shape and calling that the figure.
    path = odp_fixtures.write_loose_vector_shapes(tmp_path / 'libre.odp')
    document = read_odp(path)
    figure = next(block for block in document.pages[0].blocks if block.kind == 'figure')
    assert figure.preview_scope == 'page'
    assert figure.preview_object_id is None
    assert len(figure.source_ref.native_object_ids) == 3
    assert any('whole page' in issue.message for issue in figure.issues)


def test_a_picture_inside_an_embedded_drawing_is_not_lost(parsed):
    drawing = _block_named(parsed, 'dessin')
    assert drawing.kind == 'figure'
    entries = [parsed.asset_by_id(asset_id).source_entry for asset_id in drawing.asset_ids]
    assert entries == ['Pictures/inside-odg.png']
    assert drawing.source_text == 'Sommet A\nSommet B'
    assert drawing.preview_required is True
    assert drawing.source_completeness == 'incomplete'


def test_a_drawing_never_claims_its_relations_were_read(parsed):
    drawing = _block_named(parsed, 'dessin')
    assert drawing.content == ''
    message = ' '.join(issue.message for issue in drawing.issues)
    assert 'directions are not extracted' in message


def test_a_link_that_exists_only_on_a_hidden_slide_stays_out(tmp_path):
    # An address is content too: one that appears only on a hidden slide, or in
    # speaker notes, says what that hidden material is about.
    path = odp_fixtures.write_hidden_external_reference(tmp_path / 'cache.odp')
    document = read_odp(path)
    payload = json.dumps(document.to_dict(), ensure_ascii=False)
    assert document.external_references == ()
    assert 'hidden.invalid' not in payload
    assert 'notes.invalid' not in payload


def test_a_link_on_a_visible_slide_is_still_reported(parsed):
    assert parsed.external_references == ('https://example.invalid/figure.png',)


def test_each_page_uses_the_size_of_its_own_master(tmp_path):
    path = odp_fixtures.write_mixed_masters(tmp_path / 'masters.odp')
    document = read_odp(path)
    assert [(page.width_cm, page.height_cm) for page in document.pages[:2]] == [(28.0, 21.0), (14.0, 10.0)]
    # A page whose master we cannot resolve gets no size and says so, instead
    # of borrowing the first layout in the file.
    third = document.pages[2]
    assert (third.width_cm, third.height_cm) == (None, None)
    assert all(block.source_ref.bbox is None for block in third.blocks)
    assert any(issue.code == 'source_geometry_unverified' for issue in third.issues)
    assert any(issue.code == 'source_geometry_unverified' for issue in document.issues)


def test_a_bbox_is_computed_against_the_page_that_carries_it(tmp_path):
    path = odp_fixtures.write_mixed_masters(tmp_path / 'masters.odp')
    document = read_odp(path)
    first = document.pages[0].blocks[0].source_ref.bbox
    second = document.pages[1].blocks[0].source_ref.bbox
    # The same 20cm wide frame covers more of the 14cm page than of the 28cm one.
    assert second[2] > first[2]


def test_an_external_picture_is_reported_and_not_fetched(parsed):
    remote = _block_named(parsed, 'distant')
    assert remote.asset_ids == ()
    assert remote.source_completeness == 'incomplete'
    assert any(issue.code == 'extraction_failure' for issue in remote.issues)
    assert parsed.external_references == ('https://example.invalid/figure.png',)


def test_an_object_the_reader_cannot_classify_becomes_an_issue(parsed):
    page = next(page for page in parsed.pages if page.source_slide_no == 4)
    messages = [issue.message for issue in page.issues if issue.code == 'extraction_failure']
    assert any('draw:control' in message for message in messages)
    assert page.state == 'partial'


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------

def test_a_formula_keeps_its_object_and_is_never_called_latex(parsed):
    formula = _block_named(parsed, 'formule')
    assert formula.kind == 'formula'
    assert formula.content_format == 'plain'
    assert formula.content == 'd = sqrt{x + y}'
    asset = parsed.asset_by_id(formula.asset_ids[0])
    assert asset.kind == 'formula_source'
    assert asset.mime == 'application/mathml+xml'
    assert any(issue.code == 'content_review' for issue in formula.issues)


def test_a_formula_block_cannot_be_stored_as_latex():
    with pytest.raises(IRError):
        ParsedBlock(block_id='b', page_id='p', order_index=0, kind='formula', content='\\sqrt{x}',
                    content_format='latex',
                    source_ref=SourceRef(file_sha256='x', source_slide_no=1))


# ---------------------------------------------------------------------------
# Identity and geometry
# ---------------------------------------------------------------------------

def test_identity_is_stable_for_the_same_file(deck, tmp_path):
    first = read_odp(deck)
    copy = tmp_path / 'copie.odp'
    copy.write_bytes(deck.read_bytes())
    second = read_odp(copy)
    assert [page.page_id for page in first.pages] == [page.page_id for page in second.pages]
    assert [block.block_id for _, block in first.all_blocks()] == [b.block_id for _, b in second.all_blocks()]


def test_identity_does_not_survive_a_different_file(deck, tmp_path):
    other = odp_fixtures.write_course_deck(tmp_path / 'autre.odp')
    with zipfile.ZipFile(other, 'a') as archive:
        archive.writestr('extra.txt', 'one more byte of difference')
    first, second = read_odp(deck), read_odp(other)
    assert first.source_sha256 != second.source_sha256
    assert not set(page.page_id for page in first.pages) & set(page.page_id for page in second.pages)


def test_a_block_carries_its_native_object_path_and_bbox(parsed):
    code = _block_named(parsed, 'code')
    assert code.source_ref.native_object_ids[0].endswith('draw:page[1]/draw:frame[2]')
    assert code.source_ref.identity_version == IDENTITY_VERSION
    assert code.source_ref.bbox_basis == 'native_odf_geometry'
    x0, y0, x1, y1 = code.source_ref.bbox
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_reading_order_is_reported_when_the_geometry_disagrees(tmp_path):
    path = odp_fixtures.write_course_deck(tmp_path / 'ordre.odp')
    with zipfile.ZipFile(path) as archive:
        content = archive.read('content.xml').decode('utf-8')
    # Put the code frame above the title on the slide without moving it in the XML.
    moved = content.replace('<draw:frame draw:name="code" svg:x="1cm" svg:y="5cm"',
                            '<draw:frame draw:name="code" svg:x="1cm" svg:y="0.2cm"')
    assert moved != content
    rewritten = tmp_path / 'ordre2.odp'
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, 'w') as target:
        for info in source.infolist():
            target.writestr(info.filename, moved if info.filename == 'content.xml' else source.read(info.filename))
    document = read_odp(rewritten)
    page = next(page for page in document.pages if page.source_slide_no == 1)
    assert any(issue.code == 'source_geometry_unverified' for issue in page.issues)


def test_the_preview_page_number_is_not_filled_by_the_reader(parsed):
    assert all(page.preview_page_no is None for page in parsed.pages)
    assert all(page.preview_mapping_check == 'not_attempted' for page in parsed.pages)


# ---------------------------------------------------------------------------
# Package safety
# ---------------------------------------------------------------------------

def test_a_path_that_escapes_the_package_is_refused(tmp_path):
    path = odp_fixtures.write_path_escape(tmp_path / 'escape.odp')
    with pytest.raises(OdpReadError, match='escapes'):
        read_odp(path)


def test_a_doctype_declaration_is_refused(tmp_path):
    path = odp_fixtures.write_doctype(tmp_path / 'doctype.odp')
    with pytest.raises(OdpReadError, match='doctype'):
        read_odp(path)


def test_a_doctype_after_a_long_comment_is_refused(tmp_path):
    # Checking a fixed number of leading bytes is not a check: a comment pushes
    # the declaration past it and the entity is expanded anyway.
    path = odp_fixtures.write_late_doctype(tmp_path / 'tardif.odp', comment_bytes=9000)
    with pytest.raises(OdpReadError, match='doctype'):
        read_odp(path)


def test_a_doctype_in_another_encoding_is_refused(tmp_path):
    path = odp_fixtures.write_utf16_doctype(tmp_path / 'utf16.odp')
    with pytest.raises(OdpReadError, match='doctype'):
        read_odp(path)


def test_an_entity_is_never_expanded_into_the_result(tmp_path):
    path = odp_fixtures.write_late_doctype(tmp_path / 'tardif.odp')
    with pytest.raises(OdpReadError):
        read_odp(path)
    # And the payload cannot appear anywhere, because nothing was parsed.
    with pytest.raises(OdfPackageError):
        open_package(path).xml('content.xml')


def test_a_package_with_the_same_part_twice_is_refused(tmp_path):
    path = odp_fixtures.write_duplicate_entry(tmp_path / 'double.odp')
    with pytest.raises(OdpReadError, match='more than once'):
        read_odp(path)


def test_a_package_with_an_alias_of_a_part_is_refused(tmp_path):
    path = odp_fixtures.write_duplicate_entry(tmp_path / 'alias.odp', alias='./content.xml')
    with pytest.raises(OdpReadError, match='ambiguous'):
        read_odp(path)


def test_a_package_whose_names_differ_only_by_case_is_refused(tmp_path):
    path = odp_fixtures.write_duplicate_entry(tmp_path / 'casse.odp', alias='Content.xml')
    with pytest.raises(OdpReadError, match='differ only by case'):
        read_odp(path)


def test_a_symlink_member_is_refused(tmp_path):
    path = odp_fixtures.write_symlink_entry(tmp_path / 'lien.odp')
    with pytest.raises(OdpReadError, match='not a plain file'):
        read_odp(path)


def test_a_huge_run_of_spaces_is_bounded_and_reported(tmp_path):
    path = odp_fixtures.write_space_run(tmp_path / 'espaces.odp', count=100_000_000)
    document = read_odp(path, options=ReaderOptions(max_space_run=64))
    block = document.pages[0].blocks[0]
    assert len(block.content) <= 70
    assert block.content.startswith('a') and block.content.endswith('b')
    assert block.source_completeness == 'incomplete'
    assert any('space bound' in issue.message for issue in block.issues)
    assert document.parse_quality() == 'partial'


def test_a_run_of_spaces_inside_the_bound_is_kept_exactly(tmp_path):
    path = odp_fixtures.write_space_run(tmp_path / 'espaces.odp', count=40)
    document = read_odp(path)
    block = document.pages[0].blocks[0]
    assert block.content == 'a' + ' ' * 40 + 'b'
    assert block.source_completeness == 'complete'


def test_an_entry_that_expands_too_far_is_refused(tmp_path):
    path = odp_fixtures.write_zip_bomb(tmp_path / 'bomb.odp')
    with pytest.raises(OdpReadError, match='expands'):
        read_odp(path)


def test_a_package_of_another_type_is_refused(tmp_path):
    path = odp_fixtures.write_wrong_type(tmp_path / 'sheet.odp')
    with pytest.raises(OdpReadError, match='declared type'):
        read_odp(path)


def test_an_entry_over_the_size_limit_is_reported_not_ignored(tmp_path):
    path = odp_fixtures.write_zip_bomb(tmp_path / 'gros.odp', payload_bytes=2 * 1024 * 1024)
    # Big enough for the XML parts, far below the 2 MB entry we want reported.
    limits = OdfLimits(max_entry_bytes=100_000, max_compression_ratio=10_000_000)
    document = read_odp(path, limits=limits)
    assert document.limit_hits and document.limit_hits[0]['entry'] == 'Pictures/bomb.bin'
    assert any(issue.code == 'extraction_failure' for issue in document.issues)


def test_a_slide_that_only_shows_its_master_is_reported_not_dropped(tmp_path):
    path = odp_fixtures.write_master_only_slide(tmp_path / 'master.odp')
    document = read_odp(path)
    # The page stays, so the deck keeps its page numbers, and the gap is named.
    assert [page.source_slide_no for page in document.pages] == [1, 2]
    first = document.pages[0]
    assert first.blocks == []
    assert first.state == 'partial'
    assert any('comes from the master page' in issue.message for issue in first.issues)
    assert document.parse_quality() == 'partial'


def test_a_deck_with_no_visible_slide_is_reported(tmp_path):
    path = odp_fixtures.write_all_hidden(tmp_path / 'toutes-cachees.odp')
    document = read_odp(path)
    assert document.pages == []
    assert any('none of them visible' in issue.message for issue in document.issues)


def test_reading_does_not_touch_the_source_file(deck):
    before = deck.read_bytes()
    stat_before = deck.stat().st_mtime_ns
    read_odp(deck)
    assert deck.read_bytes() == before
    assert deck.stat().st_mtime_ns == stat_before


def test_an_entry_the_package_refused_cannot_be_read_later(tmp_path):
    path = odp_fixtures.write_path_escape(tmp_path / 'escape.odp')
    with pytest.raises(OdfPackageError):
        open_package(path)


# ---------------------------------------------------------------------------
# Coverage and report shape
# ---------------------------------------------------------------------------

def test_every_visible_object_is_either_read_or_reported(parsed):
    # The coverage check writes an issue when it finds an object that is
    # neither. Finding none of those issues is the point.
    assert not any('were neither read nor reported' in issue.message for issue in parsed.all_issues())


def test_the_result_says_the_parse_is_partial_when_something_is_missing(parsed):
    assert parsed.parse_quality() == 'partial'
    assert parsed.to_dict()['parse_quality'] == 'partial'


def test_a_clean_deck_parses_as_complete(tmp_path):
    path = odp_fixtures.write_minimal(tmp_path / 'simple.odp', slides=3)
    document = read_odp(path)
    assert [page.source_slide_no for page in document.pages] == [1, 2, 3]
    assert document.parse_quality() == 'complete'


def test_asset_bytes_never_reach_the_report(parsed):
    payload = parsed.to_dict()
    assert payload['assets']
    assert all('data' not in asset for asset in payload['assets'])


def test_the_config_hash_changes_with_the_options(deck):
    default = read_odp(deck)
    tighter = read_odp(deck, options=ReaderOptions(max_table_cells=64))
    assert default.config_hash != tighter.config_hash
    assert default.reader_version == tighter.reader_version


def test_blank_rows_keep_their_own_width_and_covered_cells():
    from xml.etree import ElementTree as ET
    from backend.worker.odf_package import NS
    from backend.worker.odp_reader import read_table
    table = ET.fromstring(f'''<table:table xmlns:table="{NS['table']}" xmlns:text="{NS['text']}">
      <table:table-row table:number-rows-repeated="2">
        <table:table-cell table:number-columns-repeated="3"/>
      </table:table-row>
      <table:table-row><table:table-cell><text:p>value</text:p></table:table-cell></table:table-row>
      <table:table-row><table:table-cell table:number-columns-spanned="3"/>
        <table:covered-table-cell table:number-columns-repeated="2"/>
      </table:table-row>
    </table:table>''')
    grid = read_table(table, max_cells=100)
    assert [len(row['cells']) for row in grid.rows] == [3, 3, 1, 3]
    assert all(cell['covered'] for cell in grid.rows[3]['cells'][1:])
    assert grid.rows[3]['cells'][0]['columns_spanned'] == 3
