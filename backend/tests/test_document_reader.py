"""M2-C reading: page, block and asset endpoints, and open/navigate/read/grep.

Permission rules come from M1 (document_access). These tests check that every
reading entry point goes through them, that nothing leaves the revision it was
asked for, and that the tools report what they cut or could not read.
"""
import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Document, DocumentAsset, DocumentBlock, DocumentPage, DocumentRevision, DocumentSource,
)
from backend.app.services import document_reader as reader
from backend.app.services import document_versions as versions
from backend.app.services.document_cleanup import mark_deleted
from backend.tests import pdf_fixtures
from backend.tests.conftest import make_client
from backend.tests.m2c_helpers import generation, parsed, published
from backend.tests.test_structured_ingest import deck_bytes, storage, structured_on  # noqa: F401 - fixtures

T = '/api/teacher/documents'
S = '/api/documents'


async def get(db, user, url, **params):
    client = await make_client(db, user)
    async with client:
        return await client.get(url, params=params)


async def pages_of(db, rev_id):
    return (await db.scalars(select(DocumentPage).where(DocumentPage.revision_id == rev_id)
                             .order_by(DocumentPage.order_index))).all()


async def rev_of(db, rev_id):
    rev = await db.get(DocumentRevision, rev_id)
    await db.refresh(rev)
    return rev


@pytest.mark.asyncio
async def test_a_teacher_reads_the_draft_with_its_gaps(db_session, storage, tmp_path, deck_bytes):
    # MinerU failed on page 3: the page is kept, partial, with the reason.
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes,
                                      extractor=pdf_fixtures.extractor(fail_pages={3}))
    pages = await pages_of(db_session, ids['rev_id'])
    gap = next(p for p in pages if p.state == 'partial')
    resp = await get(db_session, teacher, f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/pages/{gap.page_id}")
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view['publication_state'] == 'draft' and view['processing_state'] == 'parsed'
    assert view['page']['preview_page_no'] == 3 and view['blocks'] == []
    assert view['unavailable'] and view['reviews'] == []
    assert [b['order_index'] for b in view['blocks']] == sorted(b['order_index'] for b in view['blocks'])
    assert all('source_text' in b for b in view['blocks'])
    first = await get(db_session, teacher, f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/pages/{pages[0].page_id}")
    code = next(b for b in first.json()['blocks'] if 'SELECT' in b['content'])
    assert 'SELECT\tnom,   prix' in code['content'] and code['source']['content_origin'] == 'mineru_model'
    # Another teacher cannot read it.
    stranger = await get(db_session, await db_session.get(type(teacher), ids['outsider_id']),
                         f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/pages/{pages[0].page_id}")
    assert stranger.status_code == 404


@pytest.mark.asyncio
async def test_a_student_never_reads_a_draft(db_session, storage, tmp_path):
    teacher, student, _, ids = await published(db_session, storage, tmp_path)
    # A new draft beside the published revision.
    draft = await versions.clone_revision(db_session, document_id=ids['doc_id'], teacher_id=ids['teacher_id'],
        expected_generation=await generation(db_session, ids['doc_id']), expected_revision_id=ids['rev_id'],
        reason='teacher_edit')
    await db_session.commit()
    page = (await pages_of(db_session, draft.id))[0]
    resp = await get(db_session, student, f"{S}/{ids['doc_id']}/revisions/{draft.id}/pages/{page.page_id}",
                     session_id=ids['chat_id'])
    assert resp.status_code == 404
    ok = await get(db_session, student, f"{S}/{ids['doc_id']}/revisions/{ids['rev_id']}/pages/{page.page_id}",
                   session_id=ids['chat_id'])
    assert ok.status_code == 200, ok.text
    view = ok.json()
    assert 'reviews' not in view and all('source_text' not in b and 'structure' not in b for b in view['blocks'])
    assert view['newer_revision_available'] is False


@pytest.mark.asyncio
async def test_student_reading_follows_membership_and_lifecycle(db_session, storage, tmp_path):
    teacher, student, stranger, ids = await published(db_session, storage, tmp_path)
    page = (await pages_of(db_session, ids['rev_id']))[0]
    url = f"{S}/{ids['doc_id']}/revisions/{ids['rev_id']}/pages/{page.page_id}"
    assert (await get(db_session, stranger, url, session_id=ids['stranger_chat_id'])).status_code == 404
    # Someone else's session is not a key either.
    assert (await get(db_session, stranger, url, session_id=ids['chat_id'])).status_code == 404
    assert (await get(db_session, student, url, session_id=ids['chat_id'])).status_code == 200
    await versions.set_archived(db_session, document_id=ids['doc_id'], teacher_id=ids['teacher_id'],
                                expected_generation=await generation(db_session, ids['doc_id']), archived=True)
    await db_session.commit()
    assert (await get(db_session, student, url, session_id=ids['chat_id'])).status_code == 404
    teacher_url = f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/pages/{page.page_id}"
    assert (await get(db_session, teacher, teacher_url)).status_code == 200
    await mark_deleted(db_session, document_id=ids['doc_id'], teacher_id=ids['teacher_id'],
                       expected_generation=await generation(db_session, ids['doc_id']))
    await db_session.commit()
    assert (await get(db_session, teacher, teacher_url)).status_code == 404


@pytest.mark.asyncio
async def test_assets_are_served_only_inside_their_revision(db_session, storage, tmp_path, deck_bytes):
    teacher, student, _, ids = await published(db_session, storage, tmp_path)
    rev = await rev_of(db_session, ids['rev_id'])
    page = (await pages_of(db_session, rev.id))[0]
    png = await get(db_session, student, f"{S}/{ids['doc_id']}/revisions/{rev.id}/assets/{page.preview_asset_id}",
                    session_id=ids['chat_id'])
    assert png.status_code == 200 and png.content.startswith(b'\x89PNG')
    assert png.headers['cache-control'] == 'private, no-store'
    client = await make_client(db_session, student)
    async with client:
        ranged = await client.get(f"{S}/{ids['doc_id']}/revisions/{rev.id}/preview",
                                  params={'session_id': str(ids['chat_id'])}, headers={'Range': 'bytes=0-4'})
    assert ranged.status_code == 206 and ranged.content == b'%PDF-'
    # A second document of the same teacher: its asset id is refused under the first.
    _, _, _, other = await parsed(db_session, storage, tmp_path, deck_bytes)
    foreign = (await db_session.scalars(select(DocumentAsset).join(
        DocumentSource, DocumentSource.id == DocumentAsset.source_version_id)
        .where(DocumentSource.document_id == other['doc_id']))).first()
    for user, base, params in ((student, S, {'session_id': ids['chat_id']}), (teacher, T, {})):
        resp = await get(db_session, user, f"{base}/{ids['doc_id']}/revisions/{rev.id}/assets/{foreign.id}", **params)
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_student_cannot_open_the_teacher_source_route(db_session, storage, tmp_path):
    teacher, student, _, ids = await published(db_session, storage, tmp_path)
    url = f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}/source"
    source = await get(db_session, teacher, url)
    rev = await db_session.get(DocumentRevision, ids['rev_id'])
    stored = await db_session.get(DocumentSource, rev.source_version_id)
    assert source.status_code == 200 and hashlib.sha256(source.content).hexdigest() == stored.sha256
    assert (await get(db_session, student, url)).status_code == 403


@pytest.mark.asyncio
async def test_no_response_carries_a_storage_path(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    base = f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}"
    texts = [(await get(db_session, teacher, f'{base}/outline')).text]
    for page in await pages_of(db_session, ids['rev_id']):
        texts.append((await get(db_session, teacher, f'{base}/pages/{page.page_id}')).text)
        texts.append((await get(db_session, teacher, f'{base}/read', page_id=page.page_id)).text)
    texts.append((await get(db_session, teacher, f"/api/teacher/documents/{ids['doc_id']}/revisions")).text)
    joined = '\n'.join(texts)
    assert str(storage) not in joined and '/assets/' not in joined and '/sources/' not in joined


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grep_is_literal_exact_and_ignores_descriptions(db_session, storage, tmp_path, deck_bytes):
    _, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    rev = await rev_of(db_session, ids['rev_id'])
    hit = await reader.grep(db_session, rev, reader='t', pattern='prix > 10')
    assert hit['ok'] and len(hit['items']) == 1
    item = hit['items'][0]
    block = await db_session.scalar(select(DocumentBlock).where(DocumentBlock.revision_id == rev.id,
                                                                DocumentBlock.block_id == uuid.UUID(item['block_id'])))
    assert block.content[item['range']['start']:item['range']['end']] == 'prix > 10'
    # "." is a character here, not "any character".
    assert (await reader.grep(db_session, rev, reader='t', pattern='SELECT.nom'))['items'] == []
    assert len((await reader.grep(db_session, rev, reader='t', pattern='select'))['items']) == 1
    assert (await reader.grep(db_session, rev, reader='t', pattern='select', case_sensitive=True))['items'] == []
    block.content_description = 'zebraword only in a description'
    await db_session.flush()
    assert (await reader.grep(db_session, rev, reader='t', pattern='zebraword'))['items'] == []
    await db_session.rollback()
    with pytest.raises(reader.ReaderError, match='invalid_arguments'):
        await reader.grep(db_session, rev, reader='t', pattern='')


@pytest.mark.asyncio
async def test_grep_and_open_continue_with_a_bound_cursor(db_session, storage, tmp_path):
    content = pdf_fixtures.course_pdf(45)
    _, _, _, ids = await parsed(db_session, storage, tmp_path, content)
    rev = await rev_of(db_session, ids['rev_id'])
    first = await reader.grep(db_session, rev, reader='t', pattern='Titre')
    assert len(first['items']) == 20 and first['truncated'] and first['next_cursor']
    seen = [i['block_id'] for i in first['items']]
    cursor = first['next_cursor']
    while cursor:
        more = await reader.grep(db_session, rev, reader='t', pattern='Titre', cursor=cursor)
        seen += [i['block_id'] for i in more['items']]
        cursor = more['next_cursor']
    assert len(seen) == 45 == len(set(seen))
    for bad in ({'reader': 'someone else', 'pattern': 'Titre'}, {'reader': 't', 'pattern': 'numero'}):
        with pytest.raises(reader.ReaderError, match='invalid_cursor'):
            await reader.grep(db_session, rev, cursor=first['next_cursor'], **bad)
    with pytest.raises(reader.ReaderError, match='invalid_cursor'):
        await reader.grep(db_session, rev, reader='t', pattern='Titre', cursor=first['next_cursor'][:-2] + 'xx')
    outline = await reader.open_document(db_session, rev, reader='t')
    assert len(outline['items']) == 20 and outline['page_count'] == 45
    rest = await reader.open_document(db_session, rev, reader='t', cursor=outline['next_cursor'])
    assert rest['items'][0]['order_index'] == 20
    # A cursor made for grep does not open the outline.
    with pytest.raises(reader.ReaderError, match='invalid_cursor'):
        await reader.open_document(db_session, rev, reader='t', cursor=first['next_cursor'])


@pytest.mark.asyncio
async def test_read_cuts_clearly_and_the_parts_rebuild_the_block(db_session, storage, tmp_path, deck_bytes):
    _, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes,
                                extractor=pdf_fixtures.extractor(fail_pages={3}))
    rev = await rev_of(db_session, ids['rev_id'])
    block = max((await db_session.scalars(select(DocumentBlock).where(DocumentBlock.revision_id == rev.id))).all(),
                key=lambda b: len(b.content))
    whole = await reader.read(db_session, rev, reader='t', teacher=False, block_id=block.block_id)
    assert whole['items'][0]['returned_complete_block'] and not whole['truncated']
    parts, cursor = [], None
    result = await reader.read(db_session, rev, reader='t', teacher=False, block_id=block.block_id, budget=7)
    while True:
        item = result['items'][0]
        parts.append((item['range']['start'], item['range']['end'], item['content']))
        assert item['returned_complete_block'] is False
        if not result['next_cursor']:
            break
        result = await reader.read(db_session, rev, reader='t', teacher=False, block_id=block.block_id,
                                   budget=7, cursor=result['next_cursor'])
    assert ''.join(p[2] for p in parts) == block.content
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:])) and parts[0][0] == 0
    # Reading a page lists what the page is known to miss.
    unproven = next(p for p in await pages_of(db_session, rev.id) if p.state == 'partial')
    page = await reader.read(db_session, rev, reader='t', teacher=False, page_id=unproven.page_id)
    assert page['unavailable'] and page['issues']
    with pytest.raises(reader.ReaderError, match='invalid_arguments'):
        await reader.read(db_session, rev, reader='t', teacher=False, page_id=unproven.page_id, block_id=block.block_id)


@pytest.mark.asyncio
async def test_navigate_follows_reading_order(db_session, storage, tmp_path):
    content = pdf_fixtures.course_pdf(5)
    _, _, _, ids = await parsed(db_session, storage, tmp_path, content)
    rev = await rev_of(db_session, ids['rev_id'])
    _, blocks = await reader.reading_order(db_session, rev)
    forward = await reader.navigate(db_session, rev, reader='t', anchor_block_id=blocks[1].block_id,
                                    direction='next', count=3)
    assert [i['block_id'] for i in forward['items']] == [str(b.block_id) for b in blocks[2:5]]
    back = await reader.navigate(db_session, rev, reader='t', anchor_block_id=blocks[0].block_id,
                                 direction='previous')
    assert back['items'] == [] and back['at_start']
    pages = await reader.navigate(db_session, rev, reader='t', anchor_block_id=blocks[0].block_id,
                                  direction='next', unit='page', count=2)
    assert [i['order_index'] for i in pages['items']] == [1, 2]
    with pytest.raises(reader.ReaderError, match='invalid_arguments'):
        await reader.navigate(db_session, rev, reader='t', anchor_block_id=blocks[0].block_id,
                              direction='next', count=4)


@pytest.mark.asyncio
async def test_tool_endpoints_return_the_envelope_and_error_codes(db_session, storage, tmp_path, deck_bytes):
    teacher, _, _, ids = await parsed(db_session, storage, tmp_path, deck_bytes)
    base = f"{T}/{ids['doc_id']}/revisions/{ids['rev_id']}"
    ok = (await get(db_session, teacher, f'{base}/grep', pattern='Graphes')).json()
    assert set(ok) >= {'ok', 'items', 'issues', 'truncated', 'next_cursor', 'usage', 'error'} and ok['error'] is None
    bad = await get(db_session, teacher, f'{base}/grep', pattern='x', cursor='garbage')
    assert bad.status_code == 400 and bad.json()['detail']['code'] == 'invalid_cursor'
    missing = await get(db_session, teacher, f'{base}/read', block_id=uuid.uuid4())
    assert missing.status_code == 404 and missing.json()['detail']['code'] == 'not_found'
