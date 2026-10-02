"""Behaviour of the render task protocol, its validation and the page mapping.

No LibreOffice runs here. The renderer is replaced by a small script, which is
what lets these tests check the part that matters on the worker side: what we
accept back. A real render belongs to the image build and to acceptance on real
files.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from backend.renderer import entrypoint
from backend.tests import odp_fixtures
from backend.worker.document_ir import ParsedDocument, ParsedPage
from backend.worker.odp_reader import read_odp
from backend.worker.odp_sanitize import (
    SanitizeError,
    write_object_preview_copy,
    write_render_copy,
)
from backend.worker.slide_renderer import (
    RENDERER_PROTOCOL,
    RenderFailed,
    RenderSettings,
    RendererUnavailable,
    RenderValidationError,
    apply_preview_mapping,
    attach_preview_assets,
    collect_result,
    prepare_deck_task,
    prepare_object_task,
    render,
    run_subprocess_renderer,
    verify_preview_mapping,
)

PDF_BYTES = b'%PDF-1.4\n% fake preview for the protocol tests\n'
PNG_BYTES = odp_fixtures.PNG_BYTES


@pytest.fixture
def deck(tmp_path):
    return odp_fixtures.write_course_deck(tmp_path / 'cours.odp')


@pytest.fixture
def document(deck):
    return read_odp(deck)


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _write_result(task, *, outputs, task_id=None, protocol=RENDERER_PROTOCOL, status='ok', reason=None):
    payload = {
        'protocol': protocol,
        'task_id': task_id if task_id is not None else task.task_id,
        'status': status,
        'outputs': outputs,
        'seconds': 1.5,
        'renderer_version': 'slide-renderer-m2a-1',
        'soffice_version': 'LibreOffice 7.4 (test double)',
        'reason': reason,
    }
    (task.task_dir / 'result.json').write_text(json.dumps(payload), encoding='utf-8')
    return payload


def _good_pdf(task, *, pages=3, name='out/cours.pdf'):
    target = task.task_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PDF_BYTES)
    _write_result(task, outputs=[{
        'kind': 'preview_pdf', 'path': name, 'sha256': _sha(PDF_BYTES),
        'size_bytes': target.stat().st_size, 'page_no': None,
    }])
    return lambda _path: pages


# ---------------------------------------------------------------------------
# The copy that goes to the renderer
# ---------------------------------------------------------------------------

def test_notes_are_removed_even_without_a_hidden_slide(tmp_path):
    source = odp_fixtures.write_minimal(tmp_path / 'notes.odp', slides=2, notes=True)
    copy = write_render_copy(source, tmp_path / 'copie.odp', hidden_slides=())
    assert copy.removed_notes_nodes == 2
    with zipfile.ZipFile(copy.path) as archive:
        content = archive.read('content.xml').decode('utf-8')
    assert 'presentation:notes' not in content
    assert 'Notes de la diapositive' not in content
    assert copy.slide_order == (1, 2)


def test_the_copy_drops_hidden_slides_and_keeps_the_source_untouched(deck, document, tmp_path):
    before = deck.read_bytes()
    copy = write_render_copy(deck, tmp_path / 'copie.odp', hidden_slides=document.hidden_slide_numbers)
    assert copy.removed_hidden_slides == (2,)
    assert copy.slide_order == (1, 3, 4)
    with zipfile.ZipFile(copy.path) as archive:
        content = archive.read('content.xml').decode('utf-8')
    assert 'Page masquee' not in content
    assert 'Notes secretes' not in content
    assert 'Notes visibles' not in content
    assert deck.read_bytes() == before


def test_a_deck_with_only_hidden_slides_refuses_to_render(tmp_path):
    source = odp_fixtures.write_all_hidden(tmp_path / 'cachees.odp')
    with pytest.raises(SanitizeError, match='nothing to render'):
        write_render_copy(source, tmp_path / 'copie.odp', hidden_slides=(1,))


def test_the_object_copy_keeps_one_object_on_one_slide(deck, document, tmp_path):
    before = deck.read_bytes()
    group = next(block for _, block in document.all_blocks()
                 if block.preview_object_id and block.preview_object_id.endswith('draw:g[1]'))
    copy = write_object_preview_copy(deck, tmp_path / 'objet.odp', source_slide_no=3,
                                     object_paths=block_path(group))
    with zipfile.ZipFile(copy.path) as archive:
        content = archive.read('content.xml').decode('utf-8')
    assert content.count('<draw:page') == 1
    assert 'draw:name="schema"' in content
    assert 'draw:name="feuille"' not in content
    assert copy.kept_object_path.endswith('draw:g[1]')
    assert deck.read_bytes() == before


def block_path(block) -> str:
    return block.preview_object_id


def test_the_object_copy_can_reach_an_object_inside_a_group(deck, document, tmp_path):
    picture = next(block for _, block in document.all_blocks()
                   if block.group_path and block.asset_ids)
    path = picture.source_ref.native_object_ids[0]
    assert 'draw:g[1]/' in path
    copy = write_object_preview_copy(deck, tmp_path / 'sous.odp', source_slide_no=3, object_paths=path)
    with zipfile.ZipFile(copy.path) as archive:
        content = archive.read('content.xml').decode('utf-8')
    # The group is kept as the way down to the picture, and only that picture.
    assert 'draw:name="schema"' in content
    assert content.count('draw:image') == 1
    assert 'draw:name="etiquette"' not in content
    assert 'draw:name="titre3"' not in content


def test_an_object_path_that_is_not_on_the_slide_is_refused(deck, tmp_path):
    with pytest.raises(SanitizeError, match='to keep'):
        write_object_preview_copy(
            deck, tmp_path / 'absent.odp', source_slide_no=1,
            object_paths='/office:document-content/office:body/office:presentation/draw:page[1]/draw:g[9]',
        )


# ---------------------------------------------------------------------------
# Building a task
# ---------------------------------------------------------------------------

def test_a_deck_task_describes_only_the_visible_slides(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    payload = json.loads((task.task_dir / 'task.json').read_text())
    assert payload['protocol'] == RENDERER_PROTOCOL
    assert payload['expected_page_count'] == 3
    assert payload['source_slide_numbers'] == [1, 3, 4]
    assert payload['input'] == f'in/{task.input_name}'
    assert task.input_path.is_file()
    assert task.output_dir.is_dir()


def test_two_tasks_never_share_a_directory(deck, document, tmp_path):
    first = prepare_deck_task(document, deck, tmp_path / 'tasks')
    second = prepare_deck_task(document, deck, tmp_path / 'tasks')
    assert first.task_dir != second.task_dir
    with pytest.raises(RenderValidationError, match='already exists'):
        prepare_deck_task(document, deck, tmp_path / 'tasks', task_id=first.task_id)


def test_an_object_task_carries_the_object_path(deck, tmp_path):
    task = prepare_object_task(
        deck, tmp_path / 'tasks', source_slide_no=3,
        object_paths='/office:document-content/office:body/office:presentation/draw:page[3]/draw:g[1]',
    )
    assert task.expected_page_count == 1
    assert json.loads((task.task_dir / 'task.json').read_text())['kind'] == 'object_preview'


# ---------------------------------------------------------------------------
# Running the configured renderer
# ---------------------------------------------------------------------------

def test_no_configured_renderer_is_said_plainly(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks', settings=RenderSettings())
    with pytest.raises(RendererUnavailable, match='SLIDE_RENDERER_COMMAND'):
        run_subprocess_renderer(task)


def test_a_missing_renderer_command_is_said_plainly(deck, document, tmp_path):
    settings = RenderSettings(command=('/nonexistent/renderer', '{task_dir}'))
    task = prepare_deck_task(document, deck, tmp_path / 'tasks', settings=settings)
    with pytest.raises(RendererUnavailable):
        run_subprocess_renderer(task)


def _fake_renderer_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / 'fake_renderer.py'
    script.write_text(
        'import json, os, sys, hashlib\n'
        'task_dir = sys.argv[1]\n'
        'task = json.load(open(os.path.join(task_dir, "task.json")))\n'
        'out = os.path.join(task_dir, "out")\n'
        + body,
        encoding='utf-8',
    )
    return script


def test_the_renderer_only_gets_a_clean_environment(deck, document, tmp_path, monkeypatch):
    script = _fake_renderer_script(tmp_path, (
        'open(os.path.join(task_dir, "env.json"), "w").write(json.dumps(dict(os.environ)))\n'
        'data = b"%PDF-1.4\\nfake\\n"\n'
        'open(os.path.join(out, "deck.pdf"), "wb").write(data)\n'
        'json.dump({"protocol": 1, "task_id": task["task_id"], "status": "ok", "outputs": ['
        '{"kind": "preview_pdf", "path": "out/deck.pdf", "sha256": hashlib.sha256(data).hexdigest(),'
        ' "size_bytes": len(data), "page_no": None}], "seconds": 0.1,'
        ' "renderer_version": "fake", "soffice_version": "fake"},'
        ' open(os.path.join(task_dir, "result.json"), "w"))\n'
    ))
    monkeypatch.setenv('DATABASE_URL', 'postgresql://secret@localhost/secret')
    monkeypatch.setenv('LLM_API_KEY', 'a-secret-token')
    settings = RenderSettings(command=(sys.executable, str(script), '{task_dir}'))
    task = prepare_deck_task(document, deck, tmp_path / 'tasks', settings=settings)

    outcome = render(task, page_count_reader=lambda _path: 3)

    passed = json.loads((task.task_dir / 'env.json').read_text())
    assert 'DATABASE_URL' not in passed
    assert 'LLM_API_KEY' not in passed
    assert set(passed) <= {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ'}
    assert outcome.status == 'ok'
    assert outcome.pdf is not None


def test_a_renderer_over_its_limit_fails_instead_of_retrying(deck, document, tmp_path):
    script = _fake_renderer_script(tmp_path, 'import time\ntime.sleep(5)\n')
    settings = RenderSettings(command=(sys.executable, str(script), '{task_dir}'), timeout_seconds=1)
    task = prepare_deck_task(document, deck, tmp_path / 'tasks', settings=settings)
    with pytest.raises(RenderFailed, match='limit'):
        run_subprocess_renderer(task)


# ---------------------------------------------------------------------------
# Validating what came back
# ---------------------------------------------------------------------------

def test_a_valid_result_is_accepted(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    counter = _good_pdf(task, pages=3)
    outcome = collect_result(task, page_count_reader=counter)
    assert outcome.status == 'ok'
    assert outcome.page_count == 3
    assert outcome.pdf.path.is_file()
    assert outcome.issues == []


def test_a_result_from_another_task_is_refused(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    _good_pdf(task)
    _write_result(task, task_id='someone-elses-task', outputs=[{
        'kind': 'preview_pdf', 'path': 'out/cours.pdf', 'sha256': _sha(PDF_BYTES), 'size_bytes': len(PDF_BYTES),
    }])
    with pytest.raises(RenderValidationError, match='belongs to task'):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_another_protocol_is_refused(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    _good_pdf(task)
    _write_result(task, protocol=99, outputs=[{
        'kind': 'preview_pdf', 'path': 'out/cours.pdf', 'sha256': _sha(PDF_BYTES), 'size_bytes': len(PDF_BYTES),
    }])
    with pytest.raises(RenderValidationError, match='protocol'):
        collect_result(task, page_count_reader=lambda _p: 3)


@pytest.mark.parametrize('path', ['../escape.pdf', 'out/../../escape.pdf', '/etc/passwd', 'in/cours.odp'])
def test_an_output_outside_the_task_output_directory_is_refused(deck, document, tmp_path, path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    (tmp_path / 'escape.pdf').write_bytes(PDF_BYTES)
    _write_result(task, outputs=[{'kind': 'preview_pdf', 'path': path, 'sha256': None, 'size_bytes': 10}])
    with pytest.raises(RenderValidationError):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_an_output_behind_a_symlink_is_refused(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    secret = tmp_path / 'secret.pdf'
    secret.write_bytes(PDF_BYTES)
    (task.output_dir / 'link.pdf').symlink_to(secret)
    _write_result(task, outputs=[{'kind': 'preview_pdf', 'path': 'out/link.pdf',
                                  'sha256': _sha(PDF_BYTES), 'size_bytes': len(PDF_BYTES)}])
    with pytest.raises(RenderValidationError, match='symlink'):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_a_file_that_is_not_the_declared_type_is_refused(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    target = task.output_dir / 'cours.pdf'
    target.write_bytes(b'<html>not a pdf</html>')
    _write_result(task, outputs=[{'kind': 'preview_pdf', 'path': 'out/cours.pdf',
                                  'sha256': _sha(target.read_bytes()), 'size_bytes': target.stat().st_size}])
    with pytest.raises(RenderValidationError, match='does not look like'):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_a_hash_that_does_not_match_is_refused(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    target = task.output_dir / 'cours.pdf'
    target.write_bytes(PDF_BYTES)
    _write_result(task, outputs=[{'kind': 'preview_pdf', 'path': 'out/cours.pdf',
                                  'sha256': _sha(b'other bytes'), 'size_bytes': target.stat().st_size}])
    with pytest.raises(RenderValidationError, match='declared hash'):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_a_failed_render_is_reported_with_its_reason(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    _write_result(task, outputs=[], status='failed', reason='soffice produced no PDF')
    with pytest.raises(RenderFailed, match='soffice produced no PDF'):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_a_missing_result_file_is_reported(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    with pytest.raises(RenderFailed, match='no result.json'):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_the_page_count_is_read_here_not_taken_from_the_renderer(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    # The renderer claims three pages; the file really has two.
    counter = _good_pdf(task, pages=2)
    outcome = collect_result(task, page_count_reader=counter)
    assert outcome.status == 'page_count_mismatch'
    assert [issue.code for issue in outcome.issues] == ['preview_mapping_failed']
    assert '2 pages for 3 visible slides' in outcome.issues[0].message


# ---------------------------------------------------------------------------
# Page mapping
# ---------------------------------------------------------------------------

def _document_with_pages(texts):
    """A small document whose pages carry one text block each."""
    from backend.worker.document_ir import ParsedBlock, SourceRef

    pages = []
    for index, text in enumerate(texts):
        page = ParsedPage(page_id=f'page-{index}', order_index=index, source_slide_no=index + 1)
        if text is not None:
            page.blocks.append(ParsedBlock(
                block_id=f'block-{index}', page_id=page.page_id, order_index=0, kind='text',
                content=text, source_ref=SourceRef(file_sha256='f' * 64, source_slide_no=index + 1),
            ))
        pages.append(page)
    return ParsedDocument(
        source_sha256='f' * 64, source_filename='x.odp', source_size_bytes=1,
        reader_version='test', identity_version='test', config_hash='test',
        source_slide_count=len(texts), pages=pages,
    ).validate()


def test_a_unique_anchor_confirms_the_page(deck, document):
    # A preview that really shows each slide: the page text holds that slide's
    # own words. Slide 4 has no line long enough to be an anchor, so it stays in
    # review instead of being confirmed by its position.
    page_texts = [' '.join(block.content for block in page.blocks) for page in document.pages]
    mapping = verify_preview_mapping(document, page_texts)
    assert [entry.check for entry in mapping.entries] == [
        'anchor_unique_match', 'anchor_unique_match', 'needs_visual_review',
    ]
    assert mapping.verified_pages == 2
    assert mapping.needs_review == [4]


def test_equal_page_counts_alone_never_confirm_a_mapping():
    # Three slides, three preview pages, and no text that could confirm which
    # page is which. Nothing is marked verified.
    document = _document_with_pages([None, None, None])
    mapping = verify_preview_mapping(document, ['', '', ''])
    assert mapping.verified_pages == 0
    assert [entry.check for entry in mapping.entries] == ['needs_visual_review'] * 3
    assert len(mapping.needs_review) == 3
    assert all(issue.code == 'preview_mapping_failed' for issue in mapping.issues)


def test_a_slide_on_the_wrong_preview_page_is_reported():
    document = _document_with_pages(['introduction aux graphes', 'parcours en profondeur'])
    mapping = verify_preview_mapping(document, ['parcours en profondeur', 'introduction aux graphes'])
    assert [entry.check for entry in mapping.entries] == ['anchor_not_found'] * 2
    assert len(mapping.issues) == 2


def test_a_repeated_anchor_goes_to_review_not_to_a_verdict():
    document = _document_with_pages(['le meme titre partout', 'le meme titre partout'])
    mapping = verify_preview_mapping(document, ['le meme titre partout', 'le meme titre partout'])
    assert [entry.check for entry in mapping.entries] == ['anchor_matches_but_not_unique'] * 2
    assert mapping.verified_pages == 0


def test_a_preview_with_fewer_pages_reports_the_missing_ones():
    document = _document_with_pages(['premiere diapositive lisible', 'seconde diapositive lisible'])
    mapping = verify_preview_mapping(document, ['premiere diapositive lisible'])
    assert mapping.entries[1].check == 'no_preview_page'
    assert mapping.entries[1].preview_page_no is None


def test_only_a_proven_page_keeps_its_preview_number():
    document = _document_with_pages(['page un lisible ici', 'page deux lisible ici'])
    mapping = verify_preview_mapping(document, ['page un lisible ici', 'un texte sans rapport'])
    apply_preview_mapping(document, mapping)
    assert document.pages[0].preview_page_no == 1
    assert document.pages[0].state == 'usable'
    assert document.pages[1].preview_page_no is None
    assert document.pages[1].state == 'partial'
    assert any(issue.code == 'preview_mapping_failed' for issue in document.pages[1].issues)


def test_a_page_image_is_only_attached_to_a_proven_page(tmp_path):
    from backend.worker.slide_renderer import RenderOutcome, RenderOutput

    document = _document_with_pages(['page un lisible ici', 'page deux lisible ici'])
    mapping = verify_preview_mapping(document, ['page un lisible ici', 'autre chose'])
    apply_preview_mapping(document, mapping)
    pdf = tmp_path / 'deck.pdf'
    pdf.write_bytes(PDF_BYTES)
    outcome = RenderOutcome(
        task_id='t', status='ok',
        outputs=[
            RenderOutput(kind='preview_pdf', path=pdf, sha256=_sha(PDF_BYTES), size_bytes=len(PDF_BYTES)),
            RenderOutput(kind='page_png', path=tmp_path / 'page-1.png', sha256='a' * 64, size_bytes=10, page_no=1),
            RenderOutput(kind='page_png', path=tmp_path / 'page-2.png', sha256='b' * 64, size_bytes=10, page_no=2),
        ],
        page_count=2,
    )
    created = attach_preview_assets(document, outcome)
    kinds = [asset.kind for asset in created]
    assert kinds == ['preview_pdf', 'page_preview']
    assert document.pages[0].preview_asset_id is not None
    assert document.pages[1].preview_asset_id is None


# ---------------------------------------------------------------------------
# The renderer entry point, without LibreOffice
# ---------------------------------------------------------------------------

def _task_dir_with(tmp_path, task: dict, *, input_bytes: bytes | None = b'PK\x03\x04rest') -> Path:
    task_dir = tmp_path / 'task'
    (task_dir / 'in').mkdir(parents=True)
    (task_dir / 'out').mkdir()
    if input_bytes is not None:
        (task_dir / 'in' / 'deck.odp').write_bytes(input_bytes)
    (task_dir / 'task.json').write_text(json.dumps(task), encoding='utf-8')
    return task_dir


def test_the_entry_point_refuses_an_input_outside_the_task(tmp_path):
    task_dir = _task_dir_with(tmp_path, {'protocol': 1, 'task_id': 't1', 'input': '../outside.odp'})
    with pytest.raises(entrypoint.TaskError, match='not inside the task directory'):
        entrypoint.load_task(task_dir)


def test_the_entry_point_refuses_a_foreign_protocol(tmp_path):
    task_dir = _task_dir_with(tmp_path, {'protocol': 7, 'task_id': 't1', 'input': 'in/deck.odp'})
    with pytest.raises(entrypoint.TaskError, match='protocol'):
        entrypoint.load_task(task_dir)


def test_the_entry_point_refuses_an_input_that_is_not_a_package(tmp_path):
    task_dir = _task_dir_with(tmp_path, {'protocol': 1, 'task_id': 't1', 'input': 'in/deck.odp'},
                              input_bytes=b'not a zip at all')
    with pytest.raises(entrypoint.TaskError, match='not a readable package'):
        entrypoint.load_task(task_dir)


def test_the_entry_point_refuses_a_symlinked_input(tmp_path):
    task_dir = _task_dir_with(tmp_path, {'protocol': 1, 'task_id': 't1', 'input': 'in/deck.odp'},
                              input_bytes=None)
    target = tmp_path / 'elsewhere.odp'
    target.write_bytes(b'PK\x03\x04')
    (task_dir / 'in' / 'deck.odp').symlink_to(target)
    with pytest.raises(entrypoint.TaskError, match='symlink'):
        entrypoint.load_task(task_dir)


def test_the_entry_point_writes_a_failed_result_when_it_cannot_render(tmp_path, monkeypatch):
    task_dir = _task_dir_with(tmp_path, {
        'protocol': 1, 'task_id': 't1', 'input': 'in/deck.odp', 'timeout_seconds': 5,
    })
    # No LibreOffice on this path: the failure is written down, not swallowed.
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-bin'))
    assert entrypoint.main(['entrypoint', str(task_dir)]) == 1
    payload = json.loads((task_dir / 'result.json').read_text())
    assert payload['status'] == 'failed'
    assert payload['task_id'] == 't1'
    assert 'TaskError' in payload['reason']


def test_the_worker_refuses_the_failed_result_of_the_entry_point(deck, document, tmp_path, monkeypatch):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-bin'))
    entrypoint.main(['entrypoint', str(task.task_dir)])
    with pytest.raises(RenderFailed):
        collect_result(task, page_count_reader=lambda _p: 3)


def test_settings_read_from_the_environment_are_reported(monkeypatch):
    settings = RenderSettings.from_env({
        'SLIDE_RENDERER_COMMAND': 'docker run --rm --network none -v {task_dir}:/task image:1 /task',
        'SLIDE_RENDERER_TIMEOUT_SECONDS': '90',
        'SLIDE_RENDERER_IMAGE': 'edullm-slide-renderer:1',
    })
    report = settings.as_report()
    assert report['configured'] is True
    assert report['timeout_seconds'] == 90
    assert '{task_dir}:/task' in report['command']
    assert RenderSettings.from_env({}).as_report()['configured'] is False
    assert 'docker.sock' not in ' '.join(settings.command)
    assert os.environ.get('SLIDE_RENDERER_COMMAND') is None


@pytest.mark.parametrize('task_id', ['../escape', '/tmp/escape', 'a/b'])
def test_task_id_cannot_escape_root(deck, document, tmp_path, task_id):
    with pytest.raises(RenderValidationError, match='task_id'):
        prepare_deck_task(document, deck, tmp_path / 'tasks', task_id=task_id)


def test_wrong_source_cannot_use_another_decks_hidden_list(deck, document, tmp_path):
    other = odp_fixtures.write_minimal(tmp_path / 'other.odp')
    with pytest.raises(RenderValidationError, match='source hash'):
        prepare_deck_task(document, other, tmp_path / 'tasks')


def test_sanitizing_cannot_overwrite_original(deck, document):
    before = deck.read_bytes()
    with pytest.raises(SanitizeError, match='new file'):
        write_render_copy(deck, deck, hidden_slides=document.hidden_slide_numbers)
    assert deck.read_bytes() == before


def test_identical_preview_pages_have_distinct_ids_and_retry_is_idempotent(deck, document, tmp_path):
    from backend.worker.slide_renderer import RenderOutcome, RenderOutput
    for n, page in enumerate(document.pages, 1):
        page.preview_page_no = n
    outputs = [RenderOutput('page_png', tmp_path / f'p{n}.png', 'a' * 64, 10, n)
               for n in range(1, len(document.pages) + 1)]
    outcome = RenderOutcome('test', 'ok', outputs=outputs)
    created = attach_preview_assets(document, outcome)
    assert len({a.asset_id for a in created}) == len(document.pages)
    assert attach_preview_assets(document, outcome) == []
    document.validate()


def test_symlinked_output_root_is_rejected(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    task.output_dir.rmdir()
    other = tmp_path / 'other'
    other.mkdir()
    task.output_dir.symlink_to(other, target_is_directory=True)
    counter = _good_pdf(task)
    with pytest.raises(RenderValidationError, match='symlink'):
        collect_result(task, page_count_reader=counter)


def test_entrypoint_refuses_stale_output(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    (task.output_dir / 'old.pdf').write_bytes(PDF_BYTES)
    with pytest.raises(entrypoint.TaskError, match='not empty'):
        entrypoint.render_task(task.task_dir)


def test_entrypoint_refuses_modified_input(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    task.input_path.write_bytes(task.input_path.read_bytes() + b'changed')
    with pytest.raises(entrypoint.TaskError, match='bytes'):
        entrypoint.load_task(task.task_dir)


def test_missing_requested_page_images_is_failure(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks',
                             settings=RenderSettings(want_page_images=True))
    counter = _good_pdf(task)
    with pytest.raises(RenderFailed, match='page images'):
        collect_result(task, page_count_reader=counter)

@pytest.mark.parametrize('change', ['missing_hash', 'bad_size', 'duplicate', 'bad_page'])
def test_result_manifest_integrity(deck, document, tmp_path, change):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks')
    counter = _good_pdf(task)
    path = task.task_dir / 'result.json'
    result = json.loads(path.read_text())
    entry = result['outputs'][0]
    if change == 'missing_hash':
        del entry['sha256']
    elif change == 'bad_size':
        entry['size_bytes'] += 1
    elif change == 'duplicate':
        result['outputs'].append(dict(entry))
    else:
        entry['page_no'] = 1
    path.write_text(json.dumps(result))
    with pytest.raises(RenderValidationError):
        collect_result(task, page_count_reader=counter)


def test_renderer_nonzero_exit_is_failure(deck, document, tmp_path):
    task = prepare_deck_task(document, deck, tmp_path / 'tasks', settings=RenderSettings(
        command=(sys.executable, '-c', 'raise SystemExit(7)')))
    with pytest.raises(RenderFailed, match='status 7'):
        run_subprocess_renderer(task)


def test_renderer_live_output_budget_stops_process(tmp_path):
    from backend.renderer.entrypoint import _run, caps, TaskError
    (tmp_path / 'out').mkdir()
    bounds = caps()
    bounds['output_bytes'] = 32
    command = [sys.executable, '-c',
               'import pathlib,time; pathlib.Path(' + repr(str(tmp_path / 'out' / 'huge')) +
               ').write_bytes(b"x" * 64); time.sleep(10)']
    import time
    started = time.monotonic()
    with pytest.raises(TaskError, match='byte cap'):
        _run(command, timeout=5, limits=bounds, log=tmp_path / 'render.log')
    assert time.monotonic() - started < 5


def test_renderer_process_timeout_is_reported(tmp_path):
    from backend.renderer.entrypoint import _run, caps
    (tmp_path / 'out').mkdir()
    result = _run([sys.executable, '-c', 'import time; time.sleep(10)'],
                  timeout=0.1, limits=caps(), log=tmp_path / 'render.log')
    assert result['timed_out']


def test_object_path_cannot_select_another_slide(deck, tmp_path):
    with pytest.raises(SanitizeError, match='another slide'):
        write_object_preview_copy(deck, tmp_path / 'wrong.odp', source_slide_no=1,
            object_paths='/office:document-content/office:body/office:presentation/draw:page[3]/draw:frame[1]')


def test_hidden_slide_cannot_be_rendered_as_an_object(deck, document, tmp_path):
    number = document.hidden_slide_numbers[0]
    with pytest.raises(SanitizeError, match='hidden slide'):
        write_object_preview_copy(deck, tmp_path / 'hidden.odp', source_slide_no=number,
            object_paths=f'/office:document-content/office:body/office:presentation/draw:page[{number}]/draw:frame[1]')


def test_object_task_for_block_requires_its_source(deck, document, tmp_path):
    from backend.worker.slide_renderer import object_task_for_block
    from dataclasses import replace
    page, block = next((p, b) for p, b in document.all_blocks()
                       if b.preview_required and b.preview_scope == 'object' and b.preview_object_id)
    bad = replace(block, source_ref=replace(block.source_ref, file_sha256='0' * 64))
    with pytest.raises(RenderValidationError, match='source does not match'):
        object_task_for_block(bad, deck, tmp_path / 'tasks', source_slide_no=page.source_slide_no)
