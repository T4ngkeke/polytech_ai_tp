"""Prevent reasoning transcripts and title guesses from entering search indexes."""

from types import SimpleNamespace

import pytest

from backend.tests.test_ingest import _chunks_of, _seed_document, fake_embed
from backend.worker.context import clean_context, parse_context_response
from backend.worker.ingest import ingest_document
from backend.worker.main import _make_real_context_fn


@pytest.mark.parametrize('raw', [
    '{"context":"The function rejects negative inputs before recursing."}',
    '<think>The user wants a context. I should explain it.</think>\n{"context":"The function rejects negative inputs before recursing."}',
    'The user wants me to summarize.\n</think>\n{"context":"The function rejects negative inputs before recursing."}',
    '<analysis>Private reasoning</analysis>\n```json\n{"context":"The function rejects negative inputs before recursing."}\n```',
])
def test_only_final_json_context_is_accepted(raw):
    assert parse_context_response(raw) == 'The function rejects negative inputs before recursing.'


@pytest.mark.parametrize('raw', [
    None, '', 'The user wants me to describe this chunk.',
    '<think>Unfinished reasoning',
    'Thoughts</think>',
    '{"context":"Incomplete JSON',
    '{"context":null}', '{"context":42}', '[]',
    '{"reasoning":"private", "context":"short"}',
    '{"context":"<think>private</think> final"}',
    '{"context":"' + 'x' * 401 + '"}',
])
def test_invalid_or_unfinished_generation_is_not_indexable(raw):
    assert parse_context_response(raw) == ''


def test_storage_guard_handles_omitted_opening_think_tag():
    assert clean_context('Long reasoning\n</think>\nActual description.') == 'Actual description.'
    assert clean_context('<think>Reasoning without final answer') == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_reason, expected', [('stop', 'A recursion example.'), ('length', '')])
async def test_real_context_adapter_never_returns_raw_or_truncated_model_output(finish_reason, expected):
    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(
                content='The user wants a short context.</think>{"context":"A recursion example."}',
                reasoning_content='Separate private reasoning',
            ),
        )])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    fn = _make_real_context_fn(client, 'test')
    assert await fn('Source', 'Chunk') == expected


@pytest.mark.asyncio
async def test_unsupported_structured_output_does_not_store_free_text():
    async def create(**kwargs):
        raise ValueError('Provider does not support this response format')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert await _make_real_context_fn(client, 'test')('Source', 'Chunk') == ''


@pytest.mark.asyncio
async def test_heading_only_chunk_uses_source_title_without_calling_model(db_session, tmp_path):
    title = 'Cobalt-27 programming workshop: startup verification'
    doc = await _seed_document(db_session, tmp_path, body='# ' + title)

    async def must_not_generate(scope, text):
        raise AssertionError('A title must not trigger a guess about a device or chip')

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, context_fn=must_not_generate)
    chunk = (await _chunks_of(db_session, doc))[0]
    assert chunk.context == title
    assert chunk.content == '# ' + title
    assert doc.ingest_report['ingest_tokens_est'] == 0


@pytest.mark.asyncio
async def test_cleaned_context_is_used_for_storage_and_embeddings(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, body='A factorial function rejects negative inputs before making recursive calls.')
    indexed = []

    async def context(scope, text):
        return 'The user wants me to generate a sentence.</think>Input validation before recursion.'

    async def embed(texts):
        indexed.extend(texts)
        return await fake_embed(texts)

    await ingest_document(db_session, doc.id, embed_fn=embed, context_fn=context)
    chunk = (await _chunks_of(db_session, doc))[0]
    assert chunk.context == 'Input validation before recursion.'
    assert indexed == [chunk.context + '\n' + chunk.content]
    assert all('think' not in text and 'The user wants' not in text for text in indexed)


@pytest.mark.asyncio
async def test_invalid_context_falls_back_to_source_heading(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, body='# Recursion\nA factorial function rejects negative inputs before making recursive calls.')

    async def context(scope, text):
        return '<think>Never completed'

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, context_fn=context)
    chunks = await _chunks_of(db_session, doc)
    assert all(c.context == 'Recursion' for c in chunks)


@pytest.mark.asyncio
async def test_invalid_context_without_heading_still_indexes_original_text(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, body='Original course material without a heading.')

    async def context(scope, text):
        return '<think>Never completed'

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, context_fn=context)
    chunk = (await _chunks_of(db_session, doc))[0]
    assert chunk.context is None
    assert chunk.content == 'Original course material without a heading.'
