"""
test_ingest.py — worker ingestion pipeline.

The pipeline takes injected parse / embed / context / repair functions, so it
runs on SQLite with fakes (no live LLM, embedding service, or real PDF). Covers:
  * every document produces chunks — there is one path now, no type to branch on,
  * Contextual Retrieval (augmented text embedded; original stored),
  * the character-yield gate → `needs_review` (never silently ingest garbage),
  * VLM repair of garbled formulas, applied to PDFs only,
  * idempotent re-index, teacher-edit preservation, and failure rollback.

Exercise extraction, answer extraction and the whole segmentation half of the
pipeline are gone with the Exercises and Answers tables.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Class,
    DocChunk,
    Document,
    DocumentStatus,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.ingest import ingest_document
from backend.worker.parsing import GateResult


async def fake_embed(texts):
    return [[0.1, 0.2, 0.3] for _ in texts]


async def _seed_document(
    session,
    tmp_path,
    *,
    body: str = "para one\n\npara two",
):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    doc = await create_document(
        session, class_id=cls.id, lab_id=lab.id, filename="doc.txt",
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
    )
    return doc


async def _chunks_of(session, doc):
    return (await session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()


# --- corrigé path: answers only (no chunks, no exercises of its own) ----------

# --- CM path: chunks only -----------------------------------------------------

_PARA_ONE = ("Le codage binaire represente les nombres en base deux et sert de "
             "fondement a toute l'informatique moderne, du processeur au reseau.")
_PARA_TWO = ("La numeration hexadecimale utilise seize symboles et offre une "
             "notation compacte pour les valeurs binaires longues en memoire.")


@pytest.mark.asyncio
async def test_ingest_writes_chunks_with_tenant_metadata(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path,
                               body=f"{_PARA_ONE}\n\n{_PARA_TWO}")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    chunks = await _chunks_of(db_session, doc)
    assert len(chunks) == 2  # two paragraphs
    assert all(c.lab_id == doc.lab_id for c in chunks)
    assert all(c.class_id == doc.class_id for c in chunks)


@pytest.mark.asyncio
async def test_ingest_records_estimated_token_spend_in_report(db_session, tmp_path):
    """[v8.0 §9] Contextual-Retrieval token spend is estimated per document and
    surfaced in ingest_report — observation only (decision C): an over-budget
    document still indexes fully, the flag is just a signal for the teacher."""
    doc = await _seed_document(db_session, tmp_path,
                               body=f"{_PARA_ONE}\n\n{_PARA_TWO}")

    calls = {"n": 0}

    async def context_fn(scope, chunk):
        calls["n"] += 1
        return "This chunk is about the topic."

    # Budget deliberately tiny so the estimate exceeds it.
    await ingest_document(
        db_session, doc.id, embed_fn=fake_embed, context_fn=context_fn,
        token_budget=1,
    )

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed          # never blocked
    assert calls["n"] == 2                               # every chunk got context

    report = doc.ingest_report
    assert report["chunk_count"] == 2
    assert report["ingest_tokens_est"] > 0               # counted
    assert report["token_budget"] == 1
    assert report["over_budget"] is True                 # observed, not enforced

    # Context still written for every chunk despite being "over budget".
    chunks = await _chunks_of(db_session, doc)
    assert chunks and all(c.context for c in chunks)


@pytest.mark.asyncio
async def test_ingest_token_report_not_over_budget_without_budget(db_session, tmp_path):
    """No configured budget → the estimate is still recorded but over_budget is
    never falsely raised."""
    doc = await _seed_document(db_session, tmp_path,
                               body=f"{_PARA_ONE}\n\n{_PARA_TWO}")

    async def context_fn(scope, chunk):
        return "ctx"

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, context_fn=context_fn)

    await db_session.refresh(doc)
    report = doc.ingest_report
    assert report["ingest_tokens_est"] > 0
    assert report["token_budget"] is None
    assert report["over_budget"] is False


# --- TD/TP path: deterministic exercises, zero chunks -------------------------

# --- reconciliation report (ingest_report) ------------------------------------
# The teacher audits a short summary instead of re-reading the document. The
# numbering warnings (anomaly / gaps / collisions) were about exercise labels and
# went with them; what is left describes the chunking run.

@pytest.mark.asyncio
async def test_ingest_report_summarises_the_run(db_session, tmp_path):
    doc = await _seed_document(
        db_session, tmp_path,
        body=f"{_PARA_ONE}\n\n{_PARA_TWO}",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    report = doc.ingest_report
    assert report["chunk_count"] == 2
    for gone in ("anomaly", "gaps", "collisions", "segmenter", "exercise_count",
                 "dropped_invalid_lines"):
        assert gone not in report, f"ingest_report still carries {gone}"


# --- [v7.3] teacher edits survive re-ingestion ---------------------------------
# Idempotent rebuild is delete-and-recreate; rows the teacher hand-corrected
# (`edited_by_teacher`) are the most valuable data in the system and must not
# be wiped by a re-run.

@pytest.mark.asyncio
async def test_unedited_rows_are_rebuilt_fresh(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, body=_PARA_ONE)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    chunk = (await _chunks_of(db_session, doc))[0]
    chunk.content = "Silently drifted copy."   # NOT flagged as a teacher edit
    await db_session.commit()

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    chunks = await _chunks_of(db_session, doc)
    assert len(chunks) == 1
    assert "Silently drifted copy." not in chunks[0].content


@pytest.mark.asyncio
async def test_edited_chunk_survives_reingest(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path,
                               body=f"{_PARA_ONE}\n\n{_PARA_TWO}")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    chunk = sorted(await _chunks_of(db_session, doc), key=lambda c: c.chunk_index)[0]
    chunk.content = "Teacher-fixed chunk text."
    chunk.edited_by_teacher = True
    await db_session.commit()

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    chunks = sorted(await _chunks_of(db_session, doc), key=lambda c: c.chunk_index)
    assert len(chunks) == 2
    assert chunks[0].content == "Teacher-fixed chunk text."
    assert chunks[0].edited_by_teacher is True
    assert chunks[1].content == _PARA_TWO


# --- Contextual Retrieval (CM) -------------------------------------------------

@pytest.mark.asyncio
async def test_contextual_retrieval_embeds_augmented_stores_original(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path,
                               body="lone slide fragment")

    embedded: list[str] = []

    async def capturing_embed(texts):
        embedded.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def fake_context(full_text, chunk_text):
        return "CONTEXT: about binary coding"

    await ingest_document(
        db_session, doc.id, embed_fn=capturing_embed, context_fn=fake_context,
    )

    chunk = (await _chunks_of(db_session, doc))[0]
    # Original text stored for citation; generated context stored separately.
    assert chunk.content == "lone slide fragment"
    assert chunk.context == "CONTEXT: about binary coding"
    # The embedded text is the augmented one (context + original).
    assert any("CONTEXT: about binary coding" in t and "lone slide fragment" in t
               for t in embedded)


@pytest.mark.asyncio
async def test_contextual_retrieval_is_page_scoped_not_whole_document(db_session, tmp_path):
    """A chunk's context is generated from its own page/slide, never the whole
    document — so long docs don't get a context hallucinated from page 1."""
    doc = await _seed_document(db_session, tmp_path)

    def two_page_parse(storage_path):
        return ["alpha slide content", "beta slide content"], GateResult(ok=True)

    calls: list[tuple[str, str]] = []

    async def capturing_context(scope, chunk_text):
        calls.append((scope, chunk_text))
        return "ctx"

    await ingest_document(
        db_session, doc.id,
        embed_fn=fake_embed, context_fn=capturing_context, parse_fn=two_page_parse,
    )

    # The scope handed to context_fn for the "alpha" chunk must contain alpha but
    # NOT the other page's "beta" — i.e. it is page-scoped, not the full document.
    alpha_scope = next(scope for scope, chunk in calls if "alpha" in chunk)
    assert "alpha" in alpha_scope
    assert "beta" not in alpha_scope


@pytest.mark.asyncio
async def test_context_generation_runs_concurrently_and_stays_aligned(db_session, tmp_path):
    """[v7.3] Per-chunk context calls run concurrently (gather) — completion
    order must not scramble the chunk↔context pairing."""
    import asyncio

    doc = await _seed_document(db_session, tmp_path)

    def three_page_parse(storage_path):
        return ["alpha slide content", "beta slide content", "gamma slide content"], \
            GateResult(ok=True)

    in_flight = 0
    max_in_flight = 0

    async def slow_context(scope, chunk_text):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Earlier chunks sleep longer → later ones finish first.
        delay = {"alpha": 0.03, "beta": 0.02, "gamma": 0.01}
        await asyncio.sleep(delay[chunk_text.split()[0]])
        in_flight -= 1
        return f"CTX-{chunk_text.split()[0]}"

    await ingest_document(
        db_session, doc.id,
        embed_fn=fake_embed, context_fn=slow_context, parse_fn=three_page_parse,
    )

    chunks = await _chunks_of(db_session, doc)
    for chunk in chunks:
        assert chunk.context == f"CTX-{chunk.content.split()[0]}"
    assert max_in_flight > 1, "context calls must overlap (gather), not run serially"


# --- gate / idempotency / failure ----------------------------------------------

@pytest.mark.asyncio
async def test_bad_pdf_is_flagged_needs_review_not_ingested(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)

    def bad_parse(storage_path):
        return [], GateResult(ok=False, reason="Low character yield — looks scanned.")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, parse_fn=bad_parse)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.needs_review
    assert "scanned" in (doc.error_message or "")
    assert await _chunks_of(db_session, doc) == []


@pytest.mark.asyncio
async def test_ingest_is_idempotent_on_rerun(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path,
                               body=f"{_PARA_ONE}\n\n{_PARA_TWO}")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    assert len(await _chunks_of(db_session, doc)) == 2   # rebuilt, not doubled


async def boom_embed(texts):
    raise ValueError("embedding service down")


@pytest.mark.asyncio
async def test_ingest_marks_failed_and_rolls_back_on_indexing_error(db_session, tmp_path):
    """A genuine indexing failure (e.g. the embedding service is down) must roll
    back partial writes and mark the document failed. CM body — only the chunk
    path calls the embedder under the strict split."""
    doc = await _seed_document(db_session, tmp_path,
                               body="Un paragraphe de cours.")

    with pytest.raises(ValueError):
        await ingest_document(db_session, doc.id, embed_fn=boom_embed)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert "embedding service down" in (doc.error_message or "")
    assert await _chunks_of(db_session, doc) == []


# --- [v7.3] upstream cleaning --------------------------------------------------

@pytest.mark.asyncio
async def test_plain_text_repetition_without_positions_is_preserved(db_session, tmp_path):
    """Without PDF coordinates, a repeated line may be legitimate body text."""
    doc = await _seed_document(db_session, tmp_path)

    def three_page_parse(storage_path):
        pages = [
            "TD3 – Informatique – Polytech\n\nLe codage binaire en base deux.",
            "TD3 – Informatique – Polytech\n\nLa numeration hexadecimale.",
            "TD3 – Informatique – Polytech\n\nLes operateurs logiques.",
        ]
        return pages, GateResult(ok=True)

    await ingest_document(
        db_session, doc.id, embed_fn=fake_embed, parse_fn=three_page_parse,
    )

    chunks = await _chunks_of(db_session, doc)
    assert chunks, "content paragraphs must still be ingested"
    assert sum("TD3 – Informatique" in c.content for c in chunks) == 3
    assert any("codage binaire" in c.content for c in chunks)


@pytest.mark.asyncio
async def test_repeated_content_keeps_all_source_occurrences(db_session, tmp_path):
    """The worker preserves occurrences; search can later group duplicate hits."""
    doc = await _seed_document(db_session, tmp_path)

    notice = "Rappel: rendre le compte-rendu avant vendredi."

    def two_page_parse(storage_path):
        return [
            f"{notice}\n\nLe codage binaire en base deux.",
            f"{notice}\n\nLa numeration hexadecimale.",
        ], GateResult(ok=True)

    await ingest_document(
        db_session, doc.id, embed_fn=fake_embed, parse_fn=two_page_parse,
    )

    chunks = await _chunks_of(db_session, doc)
    notice_chunks = [c for c in chunks if notice in c.content]
    assert {c.page_no for c in notice_chunks} == {1, 2}


# --- [v8.1 item 8] VLM garbled-formula repair wiring ------------------------
# The worker injects `repair_fn` only when VLM_MODEL is configured; ingest
# applies it to PDFs only, and the audit entries land in ingest_report.


@pytest.mark.asyncio
async def test_vlm_repair_runs_for_pdf_and_lands_in_report(db_session, tmp_path):
    teacher = make_user(role=UserRole.teacher)
    db_session.add(teacher)
    await db_session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    db_session.add(lab)
    await db_session.flush()
    doc = await create_document(
        db_session, class_id=cls.id, lab_id=lab.id, filename="cours.pdf",
        content=b"%PDF-fake", uploaded_by=teacher.id, storage_root=tmp_path,
    )

    def parse(_path):
        return (["Le codage binaire donne N = (e -1)215 + (d -1)211 en pratique."],
                GateResult(ok=True))

    async def repair(pages, storage_path):
        assert storage_path.lower().endswith(".pdf")
        fixed = [pages[0].replace("215", "2^{15}").replace("211", "2^{11}")]
        return fixed, [{"page": 1, "line_start": 2, "line_end": 2,
                        "status": "replaced", "before": "…215…", "after": "…2^{15}…"}]

    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
                          parse_fn=parse, repair_fn=repair)

    chunk = (await _chunks_of(db_session, doc))[0]
    assert "2^{15}" in chunk.content
    await db_session.refresh(doc)
    assert doc.ingest_report["vlm_repairs"][0]["status"] == "replaced"
    assert doc.ingest_report["chunk_count"] == 1     # regular report intact


@pytest.mark.asyncio
async def test_vlm_repair_skipped_for_non_pdf(db_session, tmp_path):
    """md/html have nothing to render — repair_fn must not run for them."""
    doc = await _seed_document(db_session, tmp_path, body=_PARA_ONE)

    async def repair(pages, storage_path):  # must not be called
        raise AssertionError("repair_fn called for a non-PDF document")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, repair_fn=repair)
    assert len(await _chunks_of(db_session, doc)) == 1
