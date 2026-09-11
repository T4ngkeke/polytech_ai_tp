"""Regression cases for source loss, incorrect edits and incomplete indexing."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.models import DocumentStatus
from backend.tests.test_ingest import (
    _PARA_ONE, _PARA_TWO, _chunks_of, _seed_document, fake_embed,
)
from backend.worker.chunking import chunk_pages
from backend.worker.ingest import ingest_document
from backend.worker.main import _build_worker_fns, _make_real_embed_fn, make_fn_builder
from backend.worker.parsing import GateResult, extract_pdf_text, split_markdown_pages, strip_repeated_lines


@pytest.mark.parametrize("marker", ["```", "~~~", "````"])
def test_blank_lines_inside_fences_do_not_cut_code(marker):
    code = f"{marker}python\n" + "a = 1\n" * 25 + "\n" + "b = 2\n" * 25 + marker
    chunks = chunk_pages([code])
    assert len(chunks) == 1
    assert chunks[0].content == code


def test_nested_fence_examples_do_not_create_fake_sections_or_pages():
    text = "# Real\n````markdown\n```python\n# Fake\n~~~\n```\n````\n# Next\nBody"
    pages = split_markdown_pages(text)
    assert len(pages) == 2
    chunks = chunk_pages(pages, min_chars=1)
    example = next(c for c in chunks if "# Fake" in c.content)
    assert example.section == "Real"
    assert "````markdown" in example.content and example.content.endswith("````")


@pytest.mark.parametrize("text", ["x" * 5000, "长段落没有句号" * 800, "    x = 'Hello. World!'\n" * 150])
def test_long_content_is_bounded_without_losing_or_reformatting_text(text):
    chunks = chunk_pages([text], max_chars=300)
    assert len(chunks) > 1
    assert all(len(c.content) <= 300 for c in chunks)
    assert "".join(c.content for c in chunks) == text.rstrip("\n")


def test_long_fenced_code_splits_at_lines_and_repeats_only_fence_markers():
    body = "".join(f"value_{i} = 'Hello. World!'\n" for i in range(100))
    chunks = chunk_pages([f"```python\n{body}```"], max_chars=300)
    assert len(chunks) > 1
    assert all(len(c.content) <= 300 for c in chunks)
    assert all(c.content.startswith("```python\n") and c.content.endswith("\n```") for c in chunks)
    assert "".join(c.content[len("```python\n"):-len("\n```")] for c in chunks) == body
    assert all(" = 'Hello. World!'\n" in c.content for c in chunks)


def test_headings_define_sections_even_without_blank_lines():
    chunks = chunk_pages(["# Course\n## Recursion\nTermination condition\n## Loops\nLoop condition"])
    recursive = next(c for c in chunks if "Termination" in c.content)
    loop = next(c for c in chunks if "Loop condition" in c.content)
    assert recursive.section == "Course > Recursion"
    assert loop.section == "Course > Loops"
    assert "Loop condition" not in recursive.content


def test_pdf_hash_comments_are_not_guessed_to_be_headings():
    chunks = chunk_pages(["# this is a Python comment\nx = 1"], markdown=False)
    assert all(c.section is None for c in chunks)


def test_repeated_code_and_numbers_are_not_removed_without_coordinates():
    pages = [f"Example {i}\nif ready:\n    run()\n    return 0\n42" for i in range(3)]
    assert strip_repeated_lines(pages) == pages


def test_real_pdf_cleans_margins_but_keeps_repeated_body_and_numeric_values(tmp_path):
    import fitz

    path = tmp_path / "margins.pdf"
    with fitz.open() as pdf:
        for i in range(3):
            page = pdf.new_page()
            page.insert_text((72, 22), "Course header", fontsize=10)
            page.insert_text((72, 150), f"Example {i}", fontsize=10)
            page.insert_text((72, 180), "return 0", fontsize=10)
            page.insert_text((72, 210), "42", fontsize=10)
            page.insert_text((72, page.rect.height - 12), f"page {i + 1}", fontsize=10)
        pdf.save(path)
    raw = extract_pdf_text(path)
    cleaned = extract_pdf_text(path, clean_margins=True)
    assert all("Course header" in page for page in raw)
    assert all("Course header" not in page and "page " not in page for page in cleaned)
    assert all("return 0" in page and "42" in page for page in cleaned)
    assert [len(p.splitlines()) for p in raw] == [len(p.splitlines()) for p in cleaned]


@pytest.mark.asyncio
async def test_teacher_edit_follows_source_when_an_earlier_paragraph_is_inserted(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, body=f"{_PARA_ONE}\n\n{_PARA_TWO}")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    original = next(c for c in await _chunks_of(db_session, doc) if c.content == _PARA_TWO)
    original_id = original.id
    original.content = "Teacher correction for hexadecimal notation."
    original.edited_by_teacher = True
    await db_session.commit()
    inserted = "New introductory paragraph. " * 8
    Path(doc.storage_path).write_text(f"{inserted}\n\n{_PARA_ONE}\n\n{_PARA_TWO}")
    seen = []

    async def embed(texts):
        seen.extend(texts)
        return await fake_embed(texts)

    await ingest_document(db_session, doc.id, embed_fn=embed)
    rows = sorted(await _chunks_of(db_session, doc), key=lambda row: row.chunk_index)
    assert len(rows) == 3
    assert rows[1].content == _PARA_ONE
    assert rows[2].id == original_id and rows[2].edited_by_teacher
    assert rows[2].content == "Teacher correction for hexadecimal notation."
    assert rows[2].content in seen


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["changed_source", "legacy", "duplicate_source"])
async def test_uncertain_teacher_edit_match_keeps_old_index_and_requests_review(db_session, tmp_path, conflict):
    doc = await _seed_document(db_session, tmp_path, body=_PARA_ONE)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    row = (await _chunks_of(db_session, doc))[0]
    original_id = row.id
    row.content = "Teacher's work must survive."
    row.edited_by_teacher = True
    if conflict == "legacy":
        row.source_fingerprint = None
    await db_session.commit()
    if conflict == "changed_source":
        Path(doc.storage_path).write_text(_PARA_TWO)
    elif conflict == "duplicate_source":
        Path(doc.storage_path).write_text(f"{_PARA_ONE}\n\n{_PARA_ONE}")

    async def must_not_embed(texts):
        raise AssertionError("Ambiguous source should be detected before model calls")

    await ingest_document(db_session, doc.id, embed_fn=must_not_embed)
    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.needs_review
    assert str(original_id) in doc.error_message
    rows = await _chunks_of(db_session, doc)
    assert len(rows) == 1 and rows[0].id == original_id
    assert rows[0].content == "Teacher's work must survive."


@pytest.mark.asyncio
async def test_late_chunk_context_contains_local_text_not_page_opening(db_session, tmp_path):
    paragraphs = [f"Topic {i}. " + f"Detail {i} " * 30 for i in range(30)]
    doc = await _seed_document(db_session, tmp_path, body="\n\n".join(paragraphs))
    calls = []

    async def context(scope, chunk):
        calls.append((scope, chunk))
        return "Local context"

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, context_fn=context)
    scope, chunk = calls[-1]
    assert chunk == paragraphs[-1]
    assert paragraphs[-1] in scope and paragraphs[-2] in scope
    assert "Topic 0." not in scope
    assert all(len(scope) <= 4000 for scope, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing", "extra", "empty", "nan", "dimensions"])
async def test_invalid_embeddings_never_replace_existing_chunks(db_session, tmp_path, invalid):
    doc = await _seed_document(db_session, tmp_path, body=f"{_PARA_ONE}\n\n{_PARA_TWO}")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    doc_id = doc.id
    before = {row.id: row.content for row in await _chunks_of(db_session, doc)}

    async def bad_embed(texts):
        return {
            "missing": [[0.1]], "extra": [[0.1]] * 3, "empty": [[], []],
            "nan": [[float("nan")], [0.1]], "dimensions": [[0.1], [0.1, 0.2]],
        }[invalid]

    with pytest.raises(ValueError):
        await ingest_document(db_session, doc_id, embed_fn=bad_embed)
    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert {row.id: row.content for row in await _chunks_of(db_session, doc)} == before


@pytest.mark.asyncio
async def test_embedding_requests_are_batched_and_all_chunks_are_written(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.worker.ingest.EMBED_BATCH_SIZE", 2)
    paragraphs = [f"Paragraph {i}. " * 15 for i in range(5)]
    doc = await _seed_document(db_session, tmp_path, body="\n\n".join(paragraphs))
    requests = []

    async def embed(texts):
        requests.append(texts)
        return await fake_embed(texts)

    await ingest_document(db_session, doc.id, embed_fn=embed)
    assert [len(batch) for batch in requests] == [2, 2, 1]
    assert len(await _chunks_of(db_session, doc)) == 5
    assert doc.ingest_report["chunk_count"] == 5


@pytest.mark.asyncio
async def test_parse_exception_marks_document_failed(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)
    doc_id = doc.id

    def bad_parse(path):
        raise ValueError("Unreadable file")

    with pytest.raises(ValueError, match="Unreadable file"):
        await ingest_document(db_session, doc_id, embed_fn=fake_embed, parse_fn=bad_parse)
    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert doc.error_message == "Unreadable file"


@pytest.mark.asyncio
async def test_empty_output_does_not_report_success(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
                          parse_fn=lambda _: (["   "], GateResult(ok=True)))
    assert doc.status == DocumentStatus.needs_review


@pytest.mark.asyncio
async def test_successful_retry_clears_old_error(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, body=_PARA_ONE)
    doc.error_message = "Old failure"
    await db_session.commit()
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    assert doc.status == DocumentStatus.indexed
    assert doc.error_message is None


@pytest.mark.parametrize("vision", [False, True])
def test_production_worker_builder_accepts_optional_repair(monkeypatch, vision):
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kwargs: object())
    cfg = dict(api_key="test", base_url="http://localhost/v1", model="test",
               embedding_api_key="test", embedding_base_url="http://localhost/v1",
               embedding_model="test", vlm_api_key="test", vlm_base_url="http://localhost/v1",
               vlm_model="vision" if vision else "")
    functions = _build_worker_fns(cfg)
    assert callable(functions.embed_fn) and callable(functions.context_fn)
    assert callable(functions.repair_fn) if vision else functions.repair_fn is None


@pytest.mark.asyncio
async def test_failed_config_build_is_retried_for_unchanged_config():
    attempts = 0

    async def load(db):
        return {"model": "test"}

    def build(cfg):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("Temporary construction failure")
        return "ready"

    builder = make_fn_builder(load_config=load, build_fns=build)
    with pytest.raises(ValueError):
        await builder(None)
    assert await builder(None) == "ready"
    assert attempts == 2


@pytest.mark.asyncio
async def test_provider_embedding_indices_restore_input_order():
    async def create(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[2.0]),
                                     SimpleNamespace(index=0, embedding=[1.0])])

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    assert await _make_real_embed_fn(client, "test")(["first", "second"]) == [[1.0], [2.0]]


def test_heading_trailing_hash_in_language_name_is_preserved():
    chunks = chunk_pages(["# Learn C#\nAn introduction", "# Next ###\nAnother section"])
    assert chunks[0].section == "Learn C#"
    assert chunks[1].section == "Next"


def test_html_code_preserves_blank_lines_and_embedded_markdown():
    from backend.worker.parsing import html_to_markdown

    code = '```python\n# example\ntext = """a\n\n\nb"""\n```'
    markdown = html_to_markdown(f"<h1>Code</h1><pre>{code}</pre><h1>Next</h1><p>Body</p>")
    pages = split_markdown_pages(markdown)
    assert len(pages) == 2
    example = next(c for c in chunk_pages(pages) if "# example" in c.content)
    assert code in example.content
    assert example.section == "Code"


@pytest.mark.asyncio
async def test_later_embedding_batch_failure_keeps_the_previous_index(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.worker.ingest.EMBED_BATCH_SIZE", 1)
    doc = await _seed_document(db_session, tmp_path, body=f"{_PARA_ONE}\n\n{_PARA_TWO}")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    doc_id = doc.id
    before = {c.id: c.content for c in await _chunks_of(db_session, doc)}
    calls = 0

    async def failing_embed(texts):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("Second batch failed")
        return await fake_embed(texts)

    with pytest.raises(ValueError, match="Second batch"):
        await ingest_document(db_session, doc_id, embed_fn=failing_embed)
    await db_session.refresh(doc)
    assert {c.id: c.content for c in await _chunks_of(db_session, doc)} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("indices", [[0, 0], [0], [0, 2]])
async def test_provider_embedding_indices_must_cover_exactly_the_batch(indices):
    async def create(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1.0]) for i in indices])

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    with pytest.raises(ValueError, match="indices"):
        await _make_real_embed_fn(client, "test")(["first", "second"])


def test_markdown_paging_preserves_leading_code_indentation():
    code = "    x = 1\n\n    y = 2"
    assert split_markdown_pages(code + "\n") == [code]
    assert split_markdown_pages(code + "\n\n# Next\nBody")[0] == code


def test_heading_path_stays_within_database_limit_on_following_pages():
    chunks = chunk_pages(["# " + "A" * 600 + "\nFirst", "Continuation"])
    assert all(len(chunk.section) <= 512 for chunk in chunks)
