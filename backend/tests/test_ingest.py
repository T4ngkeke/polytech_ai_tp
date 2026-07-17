"""
test_ingest.py — [v7.1→v7.3] worker ingestion pipeline.

The pipeline takes injected parse / embed / context / resegment functions, so it
runs on SQLite with fakes (no live LLM, embedding service, or real PDF). Covers:
  * [v7.3] strict split: CM → chunks only (hybrid RAG); TD/TP → Exercises only
    (agentic search) — exercises never enter the chunk store,
  * [v7.3] deterministic exercise build: number = the segmentation-captured
    label, statement = the segment body — no LLM on the happy path,
  * [v7.3] numbering anomaly (duplicate numbers = sub-questions mistaken for
    exercises) or zero boundaries → one LLM re-segmentation; its failure keeps
    the regex segmentation (never fails the document),
  * Contextual Retrieval (augmented text embedded; original stored),
  * the character-yield gate → `needs_review` (never silently ingest garbage),
  * idempotent re-index and failure rollback.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Answer,
    Audience,
    Class,
    DocChunk,
    DocType,
    Document,
    DocumentStatus,
    Exercise,
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
    doc_type: DocType = DocType.CM,
    audience: Audience = Audience.student,
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
        doc_type=doc_type, audience=audience,
    )
    return doc


async def _chunks_of(session, doc):
    return (await session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()


async def _exercises_of(session, doc):
    return (await session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()


async def _answers_of(session, doc):
    return (await session.execute(
        select(Answer).where(Answer.document_id == doc.id)
    )).scalars().all()


# --- corrigé path: answers only (no chunks, no exercises of its own) ----------

@pytest.mark.asyncio
async def test_corrige_ingest_extracts_answers_by_number(db_session, tmp_path):
    """[v8.0 §9] A standalone corrigé produces `Answers` rows (segmented by
    number, body = answer text) and nothing else — it pairs to a TD's exercises
    by number later. answer_form (classified) and exercise_id (paired) stay NULL
    until hint generation."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.corrige,
        body="Exercice 1\nThe sum is 42.\n\nExercice 2\nUse a merge sort.\n",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    answers = sorted(await _answers_of(db_session, doc),
                     key=lambda a: a.number_normalized)
    assert [a.number_normalized for a in answers] == [1, 2]
    assert [a.number_raw for a in answers] == ["Exercice 1", "Exercice 2"]
    assert "42" in answers[0].answer_text
    assert "merge sort" in answers[1].answer_text
    # Classified at generation, paired at generation — both NULL at ingest.
    assert all(a.answer_form is None and a.exercise_id is None for a in answers)

    # A corrigé is neither the chunk nor the exercise path.
    assert await _chunks_of(db_session, doc) == []
    assert await _exercises_of(db_session, doc) == []


@pytest.mark.asyncio
async def test_corrige_reingest_does_not_duplicate_answers(db_session, tmp_path):
    """[v8.0 §9] Re-ingesting a corrigé rebuilds its Answers rather than
    appending duplicates (same idempotency as chunks/exercises)."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.corrige,
        body="Exercice 1\nThe sum is 42.\n\nExercice 2\nUse a merge sort.\n",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    answers = await _answers_of(db_session, doc)
    assert len(answers) == 2  # not 4


@pytest.mark.asyncio
async def test_ingested_corrige_answers_pair_to_td_exercises(db_session, tmp_path):
    """[v8.0 §9] End-to-end chain: a TD's exercises and a separate corrigé's
    answers land in the same lab; pairing then links each Answer to its Exercise
    by number — proving ingest's number_normalized matches pairing's."""
    from backend.app.services.answer_service import pair_answers

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

    async def _doc(body, doc_type):
        return await create_document(
            db_session, class_id=cls.id, lab_id=lab.id, filename=f"{doc_type.value}.txt",
            content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
            doc_type=doc_type, audience=Audience.student,
        )

    td = await _doc("Exercice 1\nSum two numbers.\n\nExercice 2\nSort a list.\n",
                    DocType.TD)
    corrige = await _doc("Exercice 1\nThe sum is 42.\n\nExercice 2\nUse a merge sort.\n",
                         DocType.corrige)
    await ingest_document(db_session, td.id, embed_fn=fake_embed)
    await ingest_document(db_session, corrige.id, embed_fn=fake_embed)

    report = await pair_answers(db_session, lab.id)
    assert report.paired == 2

    # Each answer now points at the matching exercise (same number).
    exercises = {e.number_normalized: e.id for e in await _exercises_of(db_session, td)}
    for ans in await _answers_of(db_session, corrige):
        assert ans.exercise_id == exercises[ans.number_normalized]


# --- CM path: chunks only -----------------------------------------------------

_PARA_ONE = ("Le codage binaire represente les nombres en base deux et sert de "
             "fondement a toute l'informatique moderne, du processeur au reseau.")
_PARA_TWO = ("La numeration hexadecimale utilise seize symboles et offre une "
             "notation compacte pour les valeurs binaires longues en memoire.")


@pytest.mark.asyncio
async def test_cm_ingest_writes_chunks_with_routing_metadata_no_exercises(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM,
                               body=f"{_PARA_ONE}\n\n{_PARA_TWO}")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    chunks = await _chunks_of(db_session, doc)
    assert len(chunks) == 2  # two paragraphs
    assert all(c.lab_id == doc.lab_id for c in chunks)
    # Routing metadata denormalized onto the chunk for query-time filtering.
    assert all(c.doc_type == DocType.CM for c in chunks)
    assert all(c.audience == Audience.student for c in chunks)

    # CM is not the exercise path.
    assert await _exercises_of(db_session, doc) == []


@pytest.mark.asyncio
async def test_cm_ingest_records_estimated_token_spend_in_report(db_session, tmp_path):
    """[v8.0 §9] Contextual-Retrieval token spend is estimated per document and
    surfaced in ingest_report — observation only (decision C): an over-budget
    document still indexes fully, the flag is just a signal for the teacher."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM,
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
async def test_cm_ingest_token_report_not_over_budget_without_budget(db_session, tmp_path):
    """No configured budget → the estimate is still recorded but over_budget is
    never falsely raised."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM,
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

@pytest.mark.asyncio
async def test_td_ingest_builds_exercises_deterministically(db_session, tmp_path):
    """[v7.3] number = the regex-captured label, statement = the segment body —
    no LLM call is needed on the happy path."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum two numbers.\n\nExercice 2\nSort a list.\n",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    exercises = sorted(await _exercises_of(db_session, doc),
                       key=lambda e: e.number_normalized)
    assert [e.number for e in exercises] == ["Exercice 1", "Exercice 2"]
    assert "Sum two numbers." in exercises[0].statement
    assert "Sort a list." in exercises[1].statement
    assert not hasattr(exercises[0], "solution")
    # Hints are teacher-triggered later — never generated at ingest.
    assert exercises[0].hints is None


@pytest.mark.asyncio
async def test_td_produces_zero_chunks_strict_split(db_session, tmp_path):
    """[v7.3] Exercises are answered from the Exercises table only (agentic
    search); TD/TP text must never enter the RAG chunk store."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum two numbers.\n",
    )

    embed_calls: list[str] = []

    async def counting_embed(texts):
        embed_calls.extend(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    await ingest_document(db_session, doc.id, embed_fn=counting_embed)

    assert await _chunks_of(db_session, doc) == []
    assert embed_calls == []  # no chunks → no embeddings paid for


@pytest.mark.asyncio
async def test_ingest_populates_normalized_exercise_number(db_session, tmp_path):
    """The raw label is stored verbatim; number_normalized carries the canonical
    int so query-side matching is decoupled from the printed format."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice III\nDo the thing.\n")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    ex = (await _exercises_of(db_session, doc))[0]
    assert ex.number == "Exercice III"   # raw label preserved
    assert ex.number_normalized == 3      # canonical int


@pytest.mark.asyncio
async def test_ingest_denormalizes_audience_onto_exercise(db_session, tmp_path):
    """Exercise.audience is copied from the source Document so the student
    filter can be enforced in the exercise-search WHERE clause."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 1\nDo it.\n", audience=Audience.teacher)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    ex = (await _exercises_of(db_session, doc))[0]
    assert ex.audience == Audience.teacher


# --- [v8.0] LLM-primary segmentation (Step 4 + 5) ----------------------------
# The per-page line classifier is the primary segmenter; regex is a log-only
# cross-check and a fallback. Fakes classify by a per-line rule (no live model).

import re as _re


def _heading_classifier(kind_of):
    """Build a fake ClassifyLinesFn: classify each numbered line by `kind_of`."""
    async def classify(numbered_page, context):
        items = []
        for line in numbered_page.splitlines():
            m = _re.match(r"(\d+)\| (.*)", line)
            if not m:
                continue
            line_no, text = int(m.group(1)), m.group(2)
            kind = kind_of(text)
            if kind:
                items.append({"line_no": line_no, "prefix": text[:15], "kind": kind})
        return items
    return classify


_EXERCISE_LINE = _re.compile(r"(?i)^(exercice|exercise|probl[eè]me)\b")


@pytest.mark.asyncio
async def test_classify_fn_drives_llm_segmentation(db_session, tmp_path):
    """With a classify_fn, boundaries come from the LLM line classifier and the
    report marks segmenter=llm (regex is only a cross-check)."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum two numbers.\n\nExercice 2\nSort a list.\n",
    )
    classify = _heading_classifier(
        lambda t: "exercise_heading" if _EXERCISE_LINE.match(t) else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=classify)

    exercises = sorted(await _exercises_of(db_session, doc),
                       key=lambda e: e.number_normalized)
    assert [e.number for e in exercises] == ["Exercice 1", "Exercice 2"]
    assert [e.number_normalized for e in exercises] == [1, 2]
    await db_session.refresh(doc)
    assert doc.ingest_report["segmenter"] == "llm"
    assert doc.ingest_report["dropped_invalid_lines"] == 0


@pytest.mark.asyncio
async def test_llm_zero_boundaries_falls_back_to_regex(db_session, tmp_path):
    """The classifier under-segments (finds nothing) but regex sees boundaries →
    keep the regex segmentation, mark segmenter=regex_fallback."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum.\n\nExercice 2\nSort.\n",
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
                          classify_fn=_heading_classifier(lambda t: None))

    assert len(await _exercises_of(db_session, doc)) == 2  # regex boundaries kept
    await db_session.refresh(doc)
    assert doc.ingest_report["segmenter"] == "regex_fallback"


@pytest.mark.asyncio
async def test_llm_classifier_failure_falls_back_to_regex(db_session, tmp_path):
    """A classify_fn that raises must never fail the document — regex is kept and
    the document still indexes."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum.\n\nExercice 2\nSort.\n",
    )

    async def boom(numbered_page, context):
        raise ValueError("ingest model down")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=boom)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed
    assert len(await _exercises_of(db_session, doc)) == 2
    assert doc.ingest_report["segmenter"] == "regex_fallback"


@pytest.mark.asyncio
async def test_reingest_reuses_segmentation_cache_no_classify_calls(db_session, tmp_path):
    """[Step 6] An unchanged document (same content_hash + extractor_version) on
    re-ingest reuses the cached boundaries — the classifier is not called again."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nA.\n\nExercice 2\nB.\n",
    )
    calls = {"n": 0}
    base = _heading_classifier(
        lambda t: "exercise_heading" if _EXERCISE_LINE.match(t) else None
    )

    async def counting_classify(numbered_page, context):
        calls["n"] += 1
        return await base(numbered_page, context)

    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
                          classify_fn=counting_classify)
    first = calls["n"]
    assert first > 0

    await ingest_document(db_session, doc.id, embed_fn=fake_embed,
                          classify_fn=counting_classify)
    assert calls["n"] == first  # cache hit → no new classifier calls

    exercises = sorted(await _exercises_of(db_session, doc),
                       key=lambda e: e.number_normalized)
    assert [e.number_normalized for e in exercises] == [1, 2]  # rebuilt correctly
    await db_session.refresh(doc)
    assert doc.ingest_report["segmenter"] == "cache"


@pytest.mark.asyncio
async def test_classifier_failure_does_not_poison_the_cache(db_session, tmp_path):
    """[Step 6] A regex_fallback caused by a transient classifier failure must NOT
    be cached — otherwise one outage permanently degrades the doc until its content
    or the extractor_version changes. A later ingest with a working classifier must
    re-run the LLM (cache miss) and segment properly."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nA.\n\nExercice 2\nB.\n",
    )

    async def boom(numbered_page, context):
        raise ValueError("classifier down")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=boom)
    await db_session.refresh(doc)
    assert doc.ingest_report["segmenter"] == "regex_fallback"
    assert doc.segmentation_cache is None  # degraded result is not cached

    # Classifier recovers → cache miss → LLM segmentation runs.
    good = _heading_classifier(
        lambda t: "exercise_heading" if _EXERCISE_LINE.match(t) else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=good)
    await db_session.refresh(doc)
    assert doc.ingest_report["segmenter"] == "llm"
    assert doc.segmentation_cache is not None


@pytest.mark.asyncio
async def test_roman_section_promotion_numbers_via_heading_token(db_session, tmp_path):
    """[Step 3.5] Roman section headings promoted to boundaries take the ordinal
    token, not a digit in the title: 'III - Base 2 et base 16' → 3, not 2."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="I - Codage\nintro.\n\nII - Numeration\nplus.\n\n"
             "III - Base 2 et base 16\nfin.\n",
    )
    classify = _heading_classifier(
        lambda t: "section_heading" if _re.match(r"^(I|II|III)\b", t) else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=classify)

    exercises = sorted(await _exercises_of(db_session, doc),
                       key=lambda e: (e.number_normalized or 0))
    assert [e.number_normalized for e in exercises] == [1, 2, 3]


# --- [v7.3] reconciliation report (ingest_report) ------------------------------
# The teacher audits a warning list instead of re-reading the document.

@pytest.mark.asyncio
async def test_ingest_report_flags_number_gap(db_session, tmp_path):
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nA.\n\nExercice 2\nB.\n\nExercice 4\nD.\n",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    report = doc.ingest_report
    assert report is not None
    assert report["anomaly"] == "number_gap"
    assert report["gaps"] == [3]
    assert report["exercise_count"] == 3


@pytest.mark.asyncio
async def test_ingest_report_clean_doc_has_no_warnings(db_session, tmp_path):
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nA.\n\nExercice 2\nB.\n",
    )

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    await db_session.refresh(doc)
    report = doc.ingest_report
    assert report["anomaly"] is None
    assert report["gaps"] == []
    assert report["collisions"] == []
    assert report["segmenter"] == "regex"          # no classify_fn → regex path
    assert report["dropped_invalid_lines"] == 0
    assert report["exercise_count"] == 2


@pytest.mark.asyncio
async def test_ingest_report_flags_lab_number_collision(db_session, tmp_path):
    """Two documents in one lab both claiming 'Exercice 1' — query-time
    ambiguity the teacher must resolve. The second ingest reports it."""
    doc_a = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                                 body="Exercice 1\nFrom doc A.\n")
    await ingest_document(db_session, doc_a.id, embed_fn=fake_embed)

    # Second document in the SAME lab.
    doc_b = await create_document(
        db_session, class_id=doc_a.class_id, lab_id=doc_a.lab_id,
        filename="doc-b.txt", content=b"Exercice 1\nFrom doc B.\n",
        uploaded_by=doc_a.uploaded_by, storage_root=tmp_path,
        doc_type=DocType.TD, audience=Audience.student,
    )
    await ingest_document(db_session, doc_b.id, embed_fn=fake_embed)

    await db_session.refresh(doc_b)
    collisions = doc_b.ingest_report["collisions"]
    assert collisions == [1]


@pytest.mark.asyncio
async def test_ingest_report_marks_llm_segmenter(db_session, tmp_path):
    """When the LLM classifier drives segmentation, the report records
    segmenter=llm so the teacher knows the primary path ran."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nA.\n\nExercice 2\nB.\n",
    )
    classify = _heading_classifier(
        lambda t: "exercise_heading" if _EXERCISE_LINE.match(t) else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=classify)

    await db_session.refresh(doc)
    assert doc.ingest_report["segmenter"] == "llm"
    assert doc.ingest_report["exercise_count"] == 2


# --- [v7.3] teacher edits survive re-ingestion ---------------------------------
# Idempotent rebuild is delete-and-recreate; rows the teacher hand-corrected
# (`edited_by_teacher`) are the most valuable data in the system and must not
# be wiped by a re-run.

@pytest.mark.asyncio
async def test_edited_exercise_survives_reingest(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 1\nOriginal statement.\n")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    ex = (await _exercises_of(db_session, doc))[0]
    ex.statement = "Teacher-corrected statement."
    ex.edited_by_teacher = True
    await db_session.commit()

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    exercises = await _exercises_of(db_session, doc)
    assert len(exercises) == 1
    assert exercises[0].statement == "Teacher-corrected statement."
    assert exercises[0].edited_by_teacher is True


@pytest.mark.asyncio
async def test_edited_exercise_kept_even_if_number_disappears(db_session, tmp_path):
    """Teacher work is never silently dropped — an edited exercise whose number
    no longer exists in the re-ingested doc is kept as an extra row."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 7\nRare statement.\n")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    ex = (await _exercises_of(db_session, doc))[0]
    ex.statement = "Teacher-written statement."
    ex.edited_by_teacher = True
    await db_session.commit()

    # Simulate a re-export where the doc now only contains exercise 1. A real
    # re-export is a new file → a new content_hash, which also misses the
    # segmentation cache (Step 6) so the new content is re-segmented.
    doc.content_hash = "reexport-" + uuid.uuid4().hex
    await db_session.commit()

    def new_parse(storage_path):
        return ["Exercice 1\nNew content.\n"], GateResult(ok=True)

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, parse_fn=new_parse)

    exercises = await _exercises_of(db_session, doc)
    statements = {e.statement for e in exercises}
    assert "Teacher-written statement." in statements
    assert any("New content." in s for s in statements)


@pytest.mark.asyncio
async def test_unedited_rows_are_rebuilt_fresh(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 1\nOriginal.\n")
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    ex = (await _exercises_of(db_session, doc))[0]
    ex.statement = "Silently drifted copy."   # NOT flagged as a teacher edit
    await db_session.commit()

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    exercises = await _exercises_of(db_session, doc)
    assert len(exercises) == 1
    assert "Original." in exercises[0].statement


@pytest.mark.asyncio
async def test_edited_chunk_survives_reingest(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM,
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
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM,
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
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM)

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

    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM)

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
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.TD,
                               body="Exercice 1\nSum two numbers.\n")

    await ingest_document(db_session, doc.id, embed_fn=fake_embed)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed)

    assert await _chunks_of(db_session, doc) == []       # strict split
    assert len(await _exercises_of(db_session, doc)) == 1


async def boom_embed(texts):
    raise ValueError("embedding service down")


@pytest.mark.asyncio
async def test_ingest_marks_failed_and_rolls_back_on_indexing_error(db_session, tmp_path):
    """A genuine indexing failure (e.g. the embedding service is down) must roll
    back partial writes and mark the document failed. CM body — only the chunk
    path calls the embedder under the strict split."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM,
                               body="Un paragraphe de cours.")

    with pytest.raises(ValueError):
        await ingest_document(db_session, doc.id, embed_fn=boom_embed)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert "embedding service down" in (doc.error_message or "")
    assert await _chunks_of(db_session, doc) == []


# --- [v7.3] upstream cleaning --------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_headers_are_stripped_before_chunking(db_session, tmp_path):
    """A header repeated on every page ('TD3 – Informatique') must not reach
    the chunks — it pollutes recall with near-identical garbage."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM)

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
    assert all("TD3 – Informatique" not in c.content for c in chunks)
    assert any("codage binaire" in c.content for c in chunks)


@pytest.mark.asyncio
async def test_duplicate_chunks_within_document_are_deduped(db_session, tmp_path):
    """The same paragraph appearing on several pages (below the header-strip
    threshold) is stored once — duplicates waste recall slots."""
    doc = await _seed_document(db_session, tmp_path, doc_type=DocType.CM)

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
    assert len(notice_chunks) == 1


# --- [v8.1] Segmentation disagreement → stored candidates (human confirm) ----
# When the regex cross-check and the LLM disagree on the boundary SET (content,
# not just count), BOTH candidate segmentations are kept: the doc still goes
# live on the LLM split, but the report flags the disagreement and the cache
# holds the alternate so a teacher can compare and switch without re-ingesting.


@pytest.mark.asyncio
async def test_boundary_disagreement_stores_alternate_and_flags_report(
    db_session, tmp_path
):
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum.\n\nExercice 2\nSort.\n\nExercice 3\nProve.\n",
    )
    # The classifier misses "Exercice 2" → LLM has 2 boundaries, regex has 3.
    classify = _heading_classifier(
        lambda t: "exercise_heading"
        if _EXERCISE_LINE.match(t) and "2" not in t else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=classify)

    assert len(await _exercises_of(db_session, doc)) == 2   # live on the LLM split
    await db_session.refresh(doc)
    assert doc.ingest_report["segmentation_disagreement"] is True
    alt = doc.segmentation_cache["alternate_segments"]
    assert [a["section"] for a in alt] == ["Exercice 1", "Exercice 2", "Exercice 3"]
    assert doc.segmentation_cache["chosen"] == "llm"


@pytest.mark.asyncio
async def test_no_disagreement_stores_no_alternate(db_session, tmp_path):
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum.\n\nExercice 2\nSort.\n",
    )
    classify = _heading_classifier(
        lambda t: "exercise_heading" if _EXERCISE_LINE.match(t) else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=classify)

    await db_session.refresh(doc)
    assert doc.ingest_report["segmentation_disagreement"] is False
    assert doc.segmentation_cache.get("alternate_segments") is None


@pytest.mark.asyncio
async def test_same_count_different_boundaries_is_a_disagreement(
    db_session, tmp_path
):
    """Count-equality is not agreement: 2 vs 2 with different boundary LINES
    still stores the alternate (the old abs(count) diff missed this)."""
    doc = await _seed_document(
        db_session, tmp_path, doc_type=DocType.TD,
        body="Exercice 1\nSum.\n\nExercice 2\nSort.\n",
    )
    # Classifier labels "Exercice 1" and (absurdly) "Sort." → same count as
    # regex (2) but a different boundary set.
    classify = _heading_classifier(
        lambda t: "exercise_heading"
        if _EXERCISE_LINE.match(t) and "1" in t or t.startswith("Sort")
        else None
    )
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, classify_fn=classify)

    await db_session.refresh(doc)
    assert doc.ingest_report["segmentation_disagreement"] is True
    assert doc.segmentation_cache["alternate_segments"] is not None
