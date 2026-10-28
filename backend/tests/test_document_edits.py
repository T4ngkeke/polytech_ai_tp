"""M2-C teacher changes: whole-block edit, exclude, reprocess, reviews, vision.

Every change must make a new draft from the revision the teacher saw, leave
that revision untouched, and never inherit an index as ready.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.app.main import app
from backend.app.models import (
    DocChunk, Document, DocumentAsset, DocumentBlock, DocumentPage, DocumentReview, DocumentRevision, IngestionJob,
)
from backend.app.routers.document_versions import get_describe_fn
from backend.app.services import document_versions as versions
from backend.app.services import document_storage
from backend.app.services import vision_descriptions as vision
from backend.app.services.vision_descriptions import VisionResult
from backend.tests import pdf_fixtures
from backend.tests.conftest import make_client
from backend.tests.m2c_helpers import generation, job_for, parsed, published
from backend.tests.test_structured_ingest import (  # noqa: F401 - fixtures
    deck_bytes, other_deck_bytes, run_job, seed, storage, structured_on, upload, worker_settings,
)
from backend.worker import structured_ingest as si

T = '/api/teacher/documents'


async def call(db, user, method, url, json=None):
    await db.refresh(user)  # an earlier refused request rolled back and expired it
    client = await make_client(db, user)
    async with client:
        return await client.request(method, url, json=json)


async def blocks_of(db, rev_id):
    return {b.block_id: b for b in (await db.scalars(
        select(DocumentBlock).where(DocumentBlock.revision_id == rev_id))).all()}


async def code_block(db, rev_id):
    return next(b for b in (await blocks_of(db, rev_id)).values() if 'SELECT' in b.content)


async def target(db, ids, rev_id=None):
    return {'expected_revision_id': str(rev_id or ids['rev_id']),
            'expected_generation': await generation(db, ids['doc_id'])}


@pytest.mark.asyncio
async def test_an_edit_makes_a_new_draft_and_keeps_the_old_one(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    block = await code_block(db_session, ids['rev_id'])
    native, block_id = block.content, block.block_id
    new_text = 'SELECT nom FROM produit;  -- corrige'
    resp = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{block_id}",
                      {**await target(db_session, ids), 'content': new_text})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert (out['processing_state'], out['publication_state'], out['index_ready']) == ('parsed', 'draft', False)
    assert out['parent_revision_id'] == str(ids['rev_id'])
    new_rev_id = uuid.UUID(out['revision_id'])
    old, new = (await blocks_of(db_session, ids['rev_id']))[block_id], (await blocks_of(db_session, new_rev_id))[block_id]
    await db_session.refresh(old)
    assert old.content == native and not old.edited_by_teacher
    # The native text is kept beside the teacher's, for a later reparse to compare.
    assert new.content == new_text and new.edited_by_teacher and new.source_text == native
    assert new.source_ref['content_origin'] == 'teacher' and new.index_state == 'searchable'
    assert new.source_completeness == old.source_completeness
    # Other blocks are copied as they were.
    assert len(await blocks_of(db_session, new_rev_id)) == len(await blocks_of(db_session, ids['rev_id']))
    report = (await db_session.get(DocumentRevision, new_rev_id)).report
    assert report['edits'][-1]['fields'] == ['content'] and report['index_ready'] is False
    # The generation the teacher saw is now stale.
    stale = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{block_id}",
                       {'expected_revision_id': str(new_rev_id), 'expected_generation': out['generation'] - 1,
                        'content': 'x'})
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_editing_a_published_revision_leaves_it_published(db_session, storage, tmp_path):
    teacher, _, _, ids = await published(db_session, storage, tmp_path)
    block_id = next(iter(await blocks_of(db_session, ids['rev_id'])))
    resp = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{block_id}",
                      {**await target(db_session, ids), 'content': 'Titre corrige par le professeur'})
    assert resp.status_code == 200, resp.text
    doc = await db_session.get(Document, ids['doc_id'])
    await db_session.refresh(doc)
    assert doc.current_revision_id == ids['rev_id']
    new_id = uuid.UUID(resp.json()['revision_id'])
    # The copy has no index: the old chunks are not inherited as ready.
    assert await db_session.scalar(select(DocChunk.id).where(DocChunk.revision_id == new_id)) is None
    publish = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/revisions/{new_id}/publish",
                         {'expected_generation': doc.generation, 'acknowledge_issues': True})
    assert publish.status_code == 409 and publish.json()['detail'] == 'revision_not_ready'
    # A superseded or foreign revision cannot be a parent.
    other = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{block_id}",
                       {'expected_revision_id': str(uuid.uuid4()), 'expected_generation': await generation(db_session, ids['doc_id']),
                        'content': 'x'})
    assert other.status_code == 409


@pytest.mark.asyncio
async def test_bad_edits_are_refused_without_a_new_revision(db_session, storage, tmp_path, deck_bytes):
    teacher, outsider_id, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    block = await code_block(db_session, ids['rev_id'])
    url, native = f"{T}/{ids['doc_id']}/blocks/{block.block_id}", block.content
    teacher_id = teacher.id
    before = await db_session.scalar(select(DocumentRevision.id).order_by(DocumentRevision.created_at.desc()))
    same = await call(db_session, teacher, 'PUT', url, {**await target(db_session, ids), 'content': native})
    assert same.status_code == 422 and same.json()['detail'] == 'no_change'
    kind = await call(db_session, teacher, 'PUT', url, {**await target(db_session, ids), 'kind': 'poem'})
    assert kind.status_code == 422
    long = await call(db_session, teacher, 'PUT', url, {**await target(db_session, ids),
                                                        'content_description': 'x' * 401})
    assert long.status_code == 422
    missing = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{uuid.uuid4()}",
                         {**await target(db_session, ids), 'content': 'x'})
    assert missing.status_code == 404
    from backend.app.models import User
    outsider = await db_session.get(User, ids['outsider_id'])
    foreign = await call(db_session, outsider, 'PUT', url, {**await target(db_session, ids), 'content': 'x'})
    assert foreign.status_code == 404
    assert await db_session.scalar(select(DocumentRevision.id).order_by(DocumentRevision.created_at.desc())) == before


@pytest.mark.asyncio
async def test_exclude_keeps_the_block_readable_and_can_be_undone(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    block = await code_block(db_session, ids['rev_id'])
    block_id, native = block.block_id, block.content
    url = f"{T}/{ids['doc_id']}/blocks/{block_id}/exclude"
    first = await call(db_session, teacher, 'POST', url, {**await target(db_session, ids), 'reason': 'exemple repete'})
    assert first.status_code == 200, first.text
    rev1 = uuid.UUID(first.json()['revision_id'])
    excluded = (await blocks_of(db_session, rev1))[block_id]
    assert excluded.index_state == 'excluded' and excluded.content == native
    assert excluded.structure['exclusion']['reason'] == 'exemple repete'
    again = await call(db_session, teacher, 'POST', url, await target(db_session, ids, rev1))
    assert again.status_code == 422
    undo = await call(db_session, teacher, 'POST', url, {**await target(db_session, ids, rev1), 'excluded': False})
    back = (await blocks_of(db_session, uuid.UUID(undo.json()['revision_id'])))[block_id]
    assert back.index_state == 'searchable' and 'exclusion' not in back.structure


@pytest.mark.asyncio
async def test_a_reparse_carries_proven_changes_and_reviews_the_rest(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    code = await code_block(db_session, ids['rev_id'])
    others = [b for b in (await blocks_of(db_session, ids['rev_id'])).values()
              if b.block_id != code.block_id and b.content.strip()]
    title, drifted = others[0], others[1]
    code_id, title_id, drifted_id, drifted_native = code.block_id, title.block_id, drifted.block_id, drifted.content
    edit = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{code_id}",
                      {**await target(db_session, ids), 'content': 'SELECT 1; -- professeur'})
    rev1 = uuid.UUID(edit.json()['revision_id'])
    ex = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{title_id}/exclude",
                    await target(db_session, ids, rev1))
    rev2 = uuid.UUID(ex.json()['revision_id'])
    edit2 = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{drifted_id}",
                       {**await target(db_session, ids, rev2), 'content': 'texte du professeur'})
    rev3 = uuid.UUID(edit2.json()['revision_id'])
    # Pretend rev3 was read by an older reader: the native text under the
    # teacher edit differs from what the reparse will read.
    parent_drifted = (await blocks_of(db_session, rev3))[drifted_id]
    parent_drifted.source_text = (parent_drifted.source_text or '') + ' (ancienne lecture)'
    await db_session.commit()
    resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{code_id}/reprocess",
                      await target(db_session, ids, rev3))
    assert resp.status_code == 200, resp.text
    rev4 = uuid.UUID(resp.json()['revision_id'])
    job = await job_for(db_session, rev4)
    assert job.task_kind == 'pdf_parse' and job.source_version_id == (await db_session.get(DocumentRevision, rev3)).source_version_id
    assert await run_job(db_session, storage, tmp_path, deck_bytes)
    rev = await db_session.get(DocumentRevision, rev4)
    await db_session.refresh(rev)
    assert rev.processing_state == 'parsed' and rev.reason == 'reparse' and rev.parent_revision_id == rev3
    assert rev.report['carried_forward'] == {'carried': 2, 'reviews': 1}
    assert rev.report['requested_for_block'] == str(code_id)
    new = await blocks_of(db_session, rev4)
    assert new[code_id].content == 'SELECT 1; -- professeur' and new[code_id].edited_by_teacher
    assert new[title_id].index_state == 'excluded'
    # Not proven: the reparse keeps the native text and asks.
    assert new[drifted_id].content == drifted_native and not new[drifted_id].edited_by_teacher
    review = await db_session.scalar(select(DocumentReview).where(DocumentReview.revision_id == rev4))
    assert (review.kind, review.block_id, review.candidate_content) == (
        'teacher_edit_unmatched', drifted_id, 'texte du professeur')
    assert review.source_ref['why'] == 'native_text_changed'
    listed = await call(db_session, teacher, 'GET', f"{T}/{ids['doc_id']}/revisions/{rev4}/reviews")
    assert [r['id'] for r in listed.json()] == [str(review.id)]
    confirm = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/revisions/{rev4}/reviews/{review.id}",
                         {'expected_generation': await generation(db_session, ids['doc_id']), 'review_revision': 1,
                          'status': 'confirmed'})
    assert confirm.status_code == 200, confirm.text
    rev5 = uuid.UUID(confirm.json()['revision']['revision_id'])
    assert (await blocks_of(db_session, rev5))[drifted_id].content == 'texte du professeur'
    assert confirm.json()['review']['status'] == 'confirmed'
    # A resolved review is not copied into the new draft.
    assert await db_session.scalar(select(DocumentReview.id).where(DocumentReview.revision_id == rev5)) is None
    twice = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/revisions/{rev4}/reviews/{review.id}",
                       {'expected_generation': await generation(db_session, ids['doc_id']), 'review_revision': 1,
                        'status': 'ignored'})
    assert twice.status_code == 409


@pytest.mark.asyncio
async def test_a_new_source_file_inherits_no_teacher_change(db_session, storage, tmp_path, deck_bytes, other_deck_bytes):
    teacher, _, lab, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    code = await code_block(db_session, ids['rev_id'])
    code_id = code.block_id
    await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{code_id}",
               {**await target(db_session, ids), 'content': 'SELECT 2;'})
    replaced = await upload(db_session, teacher, lab, other_deck_bytes, mode='replace',
                            replace_document_id=ids['doc_id'], expected_generation=await generation(db_session, ids['doc_id']))
    assert replaced.status_code == 201, replaced.text
    assert await run_job(db_session, storage, tmp_path, other_deck_bytes)
    new_blocks = await blocks_of(db_session, uuid.UUID(replaced.json()['revision_id']))
    assert new_blocks and not any(b.edited_by_teacher for b in new_blocks.values())
    assert await db_session.scalar(select(DocumentReview.id).where(
        DocumentReview.revision_id == uuid.UUID(replaced.json()['revision_id']))) is None


@pytest.mark.asyncio
async def test_a_failed_draft_can_be_reparsed_but_not_edited(db_session, storage, tmp_path):
    # A parse without MinerU configured leaves a failed draft.
    teacher, _, lab, _ = await seed(db_session)
    body = (await upload(db_session, teacher, lab, pdf_fixtures.course_pdf(1))).json()
    assert await si.process_structured_job(db_session, settings=worker_settings(storage, tmp_path))
    ids = {'doc_id': uuid.UUID(body['id']), 'rev_id': uuid.UUID(body['revision_id'])}
    rev = await db_session.get(DocumentRevision, ids['rev_id'])
    await db_session.refresh(rev)
    assert rev.processing_state == 'failed'
    resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/reprocess",
                      {'expected_generation': await generation(db_session, ids['doc_id'])})
    assert resp.status_code == 200 and resp.json()['processing_state'] == 'queued'
    edit = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{uuid.uuid4()}",
                      {**await target(db_session, ids), 'content': 'x'})
    assert edit.status_code == 409 and edit.json()['detail'] == 'revision_has_no_content'


# ---------------------------------------------------------------------------
# Vision candidates
# ---------------------------------------------------------------------------

def fake_vision(text='Un graphe de quatre sommets relies par des aretes.', usage=None):
    calls = []

    async def describe(image, mime, context):
        calls.append((len(image), mime, context))
        return VisionResult(text=text, model='test-vlm', latency_ms=12, usage=usage, uncertainties=['sens des fleches'])
    return describe, calls


async def figure_block(db, rev_id):
    """A figure with a picture of its own (some figures only have the page preview)."""
    from backend.app.models import DocumentAsset, DocumentBlockAsset
    return await db.scalar(select(DocumentBlock)
        .join(DocumentBlockAsset, (DocumentBlockAsset.revision_id == DocumentBlock.revision_id)
              & (DocumentBlockAsset.block_id == DocumentBlock.block_id))
        .join(DocumentAsset, DocumentAsset.id == DocumentBlockAsset.asset_id)
        .where(DocumentBlock.revision_id == rev_id, DocumentBlock.kind == 'figure',
               DocumentAsset.mime == 'image/png')
        .order_by(DocumentBlock.page_id, DocumentBlock.order_index).limit(1))


@pytest.mark.asyncio
async def test_without_a_verified_model_nothing_is_called(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    figure = await figure_block(db_session, ids['rev_id'])
    resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{figure.block_id}/describe",
                      await target(db_session, ids))
    assert resp.status_code == 409 and resp.json()['detail'] == 'model_unavailable'
    assert await db_session.scalar(select(DocumentReview.id)) is None


@pytest.mark.asyncio
async def test_a_description_is_a_candidate_until_the_teacher_confirms(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    describe, calls = fake_vision()
    app.dependency_overrides[get_describe_fn] = lambda: describe
    try:
        figure = await figure_block(db_session, ids['rev_id'])
        # A figure with native labels: the model text stays a description beside them.
        figure.content, figure.index_state = 'A  B  C  D', 'searchable'
        await db_session.commit()
        figure_id, figure_content = figure.block_id, figure.content
        code_id = (await code_block(db_session, ids['rev_id'])).block_id
        resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{figure_id}/describe",
                          await target(db_session, ids))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert calls and calls[0][1].startswith('image/')
        assert (out['kind'], out['model'], out['prompt_version'], out['latency_ms']) == (
            'vision_description', 'test-vlm', 'm2c-figure-description-1', 12)
        assert out['usage'] is None and out['usage_status'] == 'missing' and out['relations_verified'] is False
        block = (await blocks_of(db_session, ids['rev_id']))[figure_id]
        await db_session.refresh(block)
        assert block.content_description is None and block.content == figure_content
        confirm = await call(db_session, teacher, 'PUT',
                             f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/reviews/{out['id']}",
                             {'expected_generation': await generation(db_session, ids['doc_id']),
                              'review_revision': out['revision'], 'status': 'confirmed'})
        assert confirm.status_code == 200, confirm.text
        rev1 = uuid.UUID(confirm.json()['revision']['revision_id'])
        described = (await blocks_of(db_session, rev1))[figure_id]
        assert described.content_description == out['candidate_content']
        assert described.description_origin == 'model' and described.content == figure_content
        # New text for the block clears the model description of the old one.
        edit = await call(db_session, teacher, 'PUT', f"{T}/{ids['doc_id']}/blocks/{figure_id}",
                          {**await target(db_session, ids, rev1), 'content': 'Schema redessine'})
        cleared = (await blocks_of(db_session, uuid.UUID(edit.json()['revision_id'])))[figure_id]
        assert cleared.content_description is None and cleared.description_origin == 'none'
        # A code block is never described by a model.
        refused = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{code_id}/describe",
                             await target(db_session, ids))
        assert refused.status_code == 422
    finally:
        app.dependency_overrides.pop(get_describe_fn, None)


@pytest.mark.asyncio
async def test_a_checked_description_of_a_picture_without_text_is_its_searchable_text(
        db_session, storage, tmp_path, deck_bytes):
    # A photo or a diagram with no labels has nothing Search can find: the text
    # the teacher accepts becomes its content, as if the teacher had typed it.
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    describe, _ = fake_vision()
    app.dependency_overrides[get_describe_fn] = lambda: describe
    try:
        figure = await figure_block(db_session, ids['rev_id'])
        figure_id = figure.block_id
        figure.content, figure.index_state = '', 'needs_review'
        await db_session.commit()
        resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{figure_id}/describe",
                          await target(db_session, ids))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        confirm = await call(db_session, teacher, 'PUT',
                             f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/reviews/{out['id']}",
                             {'expected_generation': await generation(db_session, ids['doc_id']),
                              'review_revision': out['revision'], 'status': 'confirmed'})
        assert confirm.status_code == 200, confirm.text
        described = (await blocks_of(db_session, uuid.UUID(confirm.json()['revision']['revision_id'])))[figure_id]
        assert described.content == out['candidate_content'] and described.index_state == 'searchable'
        assert described.content_description is None and described.edited_by_teacher
        assert described.source_ref['content_origin'] == 'model_checked_by_teacher'
    finally:
        app.dependency_overrides.pop(get_describe_fn, None)


@pytest.mark.asyncio
async def test_a_too_long_description_is_refused_not_cut(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    describe, _ = fake_vision(text='x' * 401, usage={'input_tokens': 900, 'output_tokens': 120, 'source': 'reported'})
    app.dependency_overrides[get_describe_fn] = lambda: describe
    try:
        figure = await figure_block(db_session, ids['rev_id'])
        resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{figure.block_id}/describe",
                          await target(db_session, ids))
        assert resp.status_code == 422 and resp.json()['detail'] == 'description_too_long'
        assert await db_session.scalar(select(DocumentReview.id)) is None
    finally:
        app.dependency_overrides.pop(get_describe_fn, None)


def png_size(data):
    return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')


def test_a_picture_is_cut_out_of_its_page_with_a_small_margin():
    import pymupdf
    page = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1000, 500), False)
    page.set_rect(page.irect, (255, 255, 255))
    page.set_rect(pymupdf.IRect(500, 250, 1000, 500), (0, 0, 255))
    data = page.tobytes('png')
    crop = vision.crop_region(data, 'image/png', [500, 500, 1000, 1000])
    # 2% of the page around the picture, clipped at the page edge.
    assert png_size(crop) == (520, 260)
    with pymupdf.open(stream=crop, filetype='png') as doc:
        pix = doc[0].get_pixmap()
        assert pix.pixel(pix.width - 2, pix.height - 2) == (0, 0, 255)
    assert vision.crop_region(data, 'image/png', None) is None
    assert vision.crop_region(data, 'image/jpeg', [0, 0, 500, 500]) is None
    assert vision.crop_region(data, 'image/png', [0, 0, 5, 5]) is None   # too small: the whole page goes
    assert vision.crop_region(b'not a png', 'image/png', [0, 0, 500, 500]) is None


@pytest.mark.asyncio
async def test_the_model_sees_the_picture_not_the_whole_page(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    sent = []

    async def describe(image, mime, context):
        sent.append((image, mime))
        return VisionResult(text='Un schema.', model='test-vlm', latency_ms=1, usage=None)

    app.dependency_overrides[get_describe_fn] = lambda: describe
    try:
        figure = await figure_block(db_session, ids['rev_id'])
        page = await db_session.scalar(select(DocumentPage).where(
            DocumentPage.revision_id == figure.revision_id, DocumentPage.page_id == figure.page_id))
        preview = await db_session.get(DocumentAsset, page.preview_asset_id)
        preview_key, preview_sha = preview.storage_key, preview.sha256
        resp = await call(db_session, teacher, 'POST', f"{T}/{ids['doc_id']}/blocks/{figure.block_id}/describe",
                          await target(db_session, ids))
        assert resp.status_code == 200, resp.text
        image, mime = sent[0]
        page_w, page_h = png_size(document_storage.verified_path(str(storage), preview_key, preview_sha).read_bytes())
        crop_w, crop_h = png_size(image)
        # The fixture picture spans [80, 600, 900, 900] of the page, plus the margin.
        assert mime == 'image/png' and crop_w < page_w and crop_h < page_h / 2
    finally:
        app.dependency_overrides.pop(get_describe_fn, None)


def test_a_teacher_change_ends_an_unchecked_caption():
    from types import SimpleNamespace
    from backend.app.services.document_edits import CAPTION_UNCHECKED, apply_block_change, description_checked

    def figure():
        return SimpleNamespace(
            content='A  B', source_text=None, edited_by_teacher=False, source_ref={}, kind='figure',
            content_format='plain', index_state='searchable', content_description='graph TD A --> B',
            context_description=None, description_origin='model',
            structure={'model_candidate': 'graph TD A --> B', CAPTION_UNCHECKED: True})
    block = figure()
    assert not description_checked(block)
    apply_block_change(block, teacher_id=uuid.uuid4(), content_description='B hérite de A')
    assert block.description_origin == 'teacher' and description_checked(block)
    assert CAPTION_UNCHECKED not in block.structure and block.structure['model_candidate']
    block = figure()
    apply_block_change(block, teacher_id=uuid.uuid4(), content='A  B  C')
    assert block.content_description is None and block.description_origin == 'none'
    assert CAPTION_UNCHECKED not in block.structure
