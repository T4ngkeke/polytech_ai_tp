"""A read-only pass of the reader over the real ODP corpus, when it is present.

This is a smoke check, not content acceptance: it says the reader runs on real
teacher files, keeps its hands off them and leaks nothing it must not carry.
Whether the extracted content is right on those files is a human judgement and
belongs to the acceptance report.

The whole file skips when cours/odp_files is not in the working copy, so the
suite does not depend on courseware that is not committed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.worker.odp_reader import read_odp
from backend.worker.odp_sanitize import write_object_preview_copy, write_render_copy

CORPUS = Path(__file__).resolve().parents[2] / 'cours' / 'odp_files'
FILES = sorted(CORPUS.glob('*.odp')) if CORPUS.is_dir() else []

pytestmark = pytest.mark.skipif(not FILES, reason='the ODP corpus is not in this working copy')


@pytest.fixture(scope='module', params=[path.name for path in FILES])
def parsed(request):
    path = CORPUS / request.param
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    document = read_odp(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before, 'the reader modified the source file'
    return path, document


def test_the_deck_produces_pages_and_blocks(parsed):
    path, document = parsed
    assert document.pages, f'{path.name}: no visible page was produced'
    assert document.totals()['blocks'] > 0
    assert document.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_no_hidden_slide_became_a_page(parsed):
    _, document = parsed
    hidden = set(document.hidden_slide_numbers)
    assert not hidden & set(document.visible_slide_numbers)


def test_the_result_carries_no_notes_field(parsed):
    _, document = parsed
    payload = json.dumps(document.to_dict(), ensure_ascii=False)
    assert 'notes_text' not in payload


def test_every_object_is_read_or_reported(parsed):
    path, document = parsed
    unattributed = [issue for issue in document.all_issues()
                    if 'were neither read nor reported' in issue.message]
    assert not unattributed, f'{path.name}: {len(unattributed)} slides have an unattributed object'


def test_the_render_copy_of_a_real_deck_keeps_its_content_and_loses_its_notes(parsed, tmp_path):
    path, document = parsed
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    copy = write_render_copy(path, tmp_path / 'copie.odp', hidden_slides=document.hidden_slide_numbers)
    assert copy.slide_order == document.visible_slide_numbers
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    # The copy is a presentation we can read back, with the same visible content
    # and no hidden slide left in it.
    again = read_odp(copy.path)
    assert len(again.pages) == len(document.pages)
    assert again.hidden_slide_numbers == ()
    assert again.notes_slide_count == 0
    assert again.totals()['blocks'] == document.totals()['blocks']


def test_an_object_preview_copy_can_be_built_for_a_real_figure(parsed, tmp_path):
    path, document = parsed
    wanted = next(((page, block) for page, block in document.all_blocks()
                   if block.preview_required and block.preview_object_id), None)
    if wanted is None:
        pytest.skip('this deck has no object waiting for a rendered preview')
    page, block = wanted
    copy = write_object_preview_copy(path, tmp_path / 'objet.odp',
                                     source_slide_no=page.source_slide_no,
                                     object_paths=block.preview_object_id)
    again = read_odp(copy.path)
    assert len(again.pages) == 1
    # One object on the slide, and it is the one we asked for.
    assert len(again.pages[0].blocks) == 1


def test_no_formula_or_figure_claims_a_transcription(parsed):
    _, document = parsed
    for _, block in document.all_blocks():
        if block.kind == 'formula':
            assert block.content_format == 'plain'
        if block.kind == 'figure' and block.content:
            # A figure's content is never a description we wrote; native labels
            # live in source_text.
            raise AssertionError('a figure block carries generated content')
