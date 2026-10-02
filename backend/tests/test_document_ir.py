"""Invariants of the parse result.

These are the rules a later package is allowed to rely on: identities are
derived and not invented, a page always belongs to the document that lists it,
a hidden slide can never be a page, and no asset byte reaches a report.
"""
from __future__ import annotations

import pytest

from backend.worker.document_ir import (
    IRError,
    ParsedAsset,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseIssue,
    SourceRef,
    stable_id,
)

SHA = 'a' * 64


def _ref(slide_no=1):
    return SourceRef(file_sha256=SHA, source_slide_no=slide_no, identity_version='test-identity-1')


def _document(pages=None, assets=None, **kwargs):
    return ParsedDocument(
        source_sha256=SHA, source_filename='cours.odp', source_size_bytes=10,
        reader_version='test-reader', identity_version='test-identity-1', config_hash='cfg',
        pages=pages or [], assets=assets or [], **kwargs,
    )


def _page(index=0, slide_no=1, blocks=None, **kwargs):
    return ParsedPage(page_id=f'page-{slide_no}', order_index=index, source_slide_no=slide_no,
                      blocks=blocks or [], **kwargs)


def _block(page_id='page-1', index=0, slide_no=1, **kwargs):
    defaults = dict(block_id=f'block-{page_id}-{index}', page_id=page_id, order_index=index,
                    kind='text', content='texte', source_ref=_ref(slide_no), source_completeness='complete')
    defaults.update(kwargs)
    return ParsedBlock(**defaults)


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('field,value', [
    ('kind', 'diagram'),
    ('content_format', 'html'),
    ('source_completeness', 'mostly'),
])
def test_a_value_outside_the_agreed_vocabulary_fails_loudly(field, value):
    with pytest.raises(IRError):
        _block(**{field: value})


def test_an_unknown_issue_code_fails_loudly():
    with pytest.raises(IRError, match='issue code'):
        ParseIssue(code='something_went_wrong', scope='document', message='x')


def test_a_block_issue_must_name_its_block():
    with pytest.raises(IRError, match='block_id'):
        ParseIssue(code='content_review', scope='block', message='x')


def test_an_unknown_asset_kind_fails_loudly():
    with pytest.raises(IRError, match='asset kind'):
        ParsedAsset(asset_id='a', kind='thumbnail', mime='image/png', sha256=SHA, size_bytes=1)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_the_same_inputs_give_the_same_identity():
    assert stable_id('v1', SHA, 'page', '3') == stable_id('v1', SHA, 'page', '3')


@pytest.mark.parametrize('changed', [
    ('v2', SHA, 'page', '3'),
    ('v1', 'b' * 64, 'page', '3'),
    ('v1', SHA, 'page', '4'),
    ('v1', SHA, 'block', '3'),
])
def test_any_changed_part_gives_another_identity(changed):
    assert stable_id('v1', SHA, 'page', '3') != stable_id(*changed)


def test_the_parts_cannot_be_confused_by_joining_them():
    assert stable_id('v1', SHA, 'ab', 'c') != stable_id('v1', SHA, 'a', 'bc')


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_a_valid_document_validates():
    page = _page(blocks=[_block()])
    assert _document([page]).validate() is not None


def test_a_page_out_of_order_is_refused():
    with pytest.raises(IRError, match='order_index'):
        _document([_page(index=1)]).validate()


def test_a_block_that_belongs_to_another_page_is_refused():
    page = _page(blocks=[_block(page_id='page-9')])
    with pytest.raises(IRError, match='does not belong'):
        _document([page]).validate()


def test_a_block_pointing_at_another_slide_is_refused():
    page = _page(blocks=[_block(slide_no=2)])
    with pytest.raises(IRError, match='another slide'):
        _document([page]).validate()


def test_a_hidden_slide_can_never_be_a_page():
    document = _document([_page(slide_no=2)], hidden_slide_numbers=(2,))
    with pytest.raises(IRError, match='hidden'):
        document.validate()


def test_a_block_cannot_point_at_an_asset_the_document_does_not_have():
    page = _page(blocks=[_block(asset_ids=('missing-asset',))])
    with pytest.raises(IRError, match='unknown assets'):
        _document([page]).validate()


def test_an_issue_cannot_point_at_a_page_that_is_not_here():
    document = _document([_page()])
    document.issues.append(ParseIssue(code='content_review', scope='page', page_id='page-404', message='x'))
    with pytest.raises(IRError, match='points at a page'):
        document.validate()


def test_duplicate_identities_are_refused():
    document = _document([_page(index=0, slide_no=1), _page(index=1, slide_no=1)])
    with pytest.raises(IRError, match='duplicate page id'):
        document.validate()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_asset_bytes_stay_out_of_the_report():
    asset = ParsedAsset(asset_id='a1', kind='embedded_image', mime='image/png', sha256=SHA,
                        size_bytes=3, data=b'\x89PN')
    payload = _document([], [asset]).to_dict()
    assert payload['assets'][0]['sha256'] == SHA
    assert 'data' not in payload['assets'][0]
    assert b'\x89PN' not in repr(payload).encode('utf-8', 'ignore')


def test_the_report_counts_notes_without_carrying_them():
    payload = _document([], notes_slide_count=4).to_dict()
    assert payload['notes_slide_count'] == 4
    assert 'notes_text' not in payload


def test_a_document_with_a_pending_gap_is_partial():
    page = _page(blocks=[_block()])
    page.issues.append(ParseIssue(code='extraction_failure', scope='page', page_id=page.page_id,
                                  message='an object was not read'))
    assert _document([page]).parse_quality() == 'partial'


def test_a_document_whose_gaps_were_decided_is_complete():
    page = _page(blocks=[_block()])
    page.issues.append(ParseIssue(code='extraction_failure', scope='page', page_id=page.page_id,
                                  message='an object was not read', resolution='ignored'))
    assert _document([page]).parse_quality() == 'complete'


def test_a_page_that_is_not_usable_keeps_the_document_partial():
    assert _document([_page(state='partial', blocks=[_block()])]).parse_quality() == 'partial'


def test_a_review_note_alone_does_not_make_the_parse_partial():
    page = _page(blocks=[_block()])
    page.blocks[0].issues.append(ParseIssue(code='content_review', scope='block', page_id=page.page_id,
                                            block_id=page.blocks[0].block_id, message='needs a look'))
    assert _document([page]).parse_quality() == 'complete'


def test_the_totals_describe_what_was_read():
    page = _page(blocks=[_block(), _block(index=1, kind='figure', content='', preview_required=True)])
    totals = _document([page]).totals()
    assert totals == {
        'pages': 1, 'blocks': 2, 'blocks_by_kind': {'text': 1, 'figure': 1},
        'assets': 0, 'issues': 0, 'blocks_needing_preview': 1,
    }


@pytest.mark.parametrize('completeness', ['incomplete', 'unknown'])
def test_incomplete_blocks_cannot_make_a_complete_document(completeness):
    page = _page(blocks=[_block(source_completeness=completeness)])
    assert _document([page]).parse_quality() == 'partial'
