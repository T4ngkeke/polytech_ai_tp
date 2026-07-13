"""
test_segmentation.py — [v8.0] LLM-primary exercise segmenter (Step 2+3).

The segmenter replaces "regex primary + LLM narrow patch" with "small LLM
per-page line classification + document-level reconcile + deterministic
split/number". Judgment/execution are separated: the LLM only names each line's
STRUCTURAL ROLE (by line number); deterministic code numbers the lines, validates
the model's line references against the printed text, reconciles boundaries, and
slices. The printed label — never a model-generated number — becomes the exercise
number. All model calls are an injected fake here (no live model).
"""

import pytest

from backend.worker.chunking import (
    Chunk,
    StructuralLine,
    build_rolling_context,
    classify_document,
    number_lines,
    reconcile_boundaries,
    segment_exercises,
    split_into_exercises,
    validate_page_classification,
)


class ScriptedClassifier:
    """A fake ClassifyLinesFn that returns queued per-page responses (or raises
    a queued Exception), recording every (numbered_page, context) call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, numbered_page, context):
        self.calls.append((numbered_page, context))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_number_lines_prefixes_each_line_with_a_1_indexed_number():
    text = "Exercice 1\nÉcrire une fonction.\n\nExercice 2"
    numbered = number_lines(text)
    assert numbered == (
        "1| Exercice 1\n"
        "2| Écrire une fonction.\n"
        "3| \n"
        "4| Exercice 2"
    )


# --- Step 2.4: line-reference validation (anti-hallucination guard) ----------
# The model returns {line_no, prefix, kind}; code re-reads that line and confirms
# the prefix matches, correcting a ±1 off-by-one, else dropping the item. There is
# no verbatim text anchor, so a model that reworded the line can't silently drop it.

_PAGE = "Exercice 1\nÉcrire une fonction récursive.\nExercice 2\nTrier une liste."


def test_valid_item_is_kept_with_the_real_line_text():
    items = [{"line_no": 1, "prefix": "Exercice 1", "kind": "exercise_heading"}]
    valid, dropped = validate_page_classification(_PAGE, items, page=1)
    assert dropped == 0
    assert len(valid) == 1
    line = valid[0]
    assert line.page == 1
    assert line.line_no == 1
    assert line.kind == "exercise_heading"
    assert line.text == "Exercice 1"


def test_off_by_one_below_is_corrected():
    # Model said line 2, but "Exercice 2" is actually on line 3 → correct to 3.
    items = [{"line_no": 2, "prefix": "Exercice 2", "kind": "exercise_heading"}]
    valid, dropped = validate_page_classification(_PAGE, items, page=1)
    assert dropped == 0
    assert valid[0].line_no == 3
    assert valid[0].text == "Exercice 2"


def test_off_by_one_above_is_corrected():
    # Model said line 4, "Exercice 2" is on line 3 → correct to 3.
    items = [{"line_no": 4, "prefix": "Exercice 2", "kind": "exercise_heading"}]
    valid, dropped = validate_page_classification(_PAGE, items, page=1)
    assert dropped == 0
    assert valid[0].line_no == 3


def test_line_no_out_of_range_is_dropped_and_counted():
    items = [{"line_no": 99, "prefix": "Exercice 9", "kind": "exercise_heading"}]
    valid, dropped = validate_page_classification(_PAGE, items, page=1)
    assert valid == []
    assert dropped == 1


def test_prefix_that_matches_no_nearby_line_is_dropped():
    # line_no in range but the prefix is nowhere near (not at 1, 2, or 3).
    items = [{"line_no": 2, "prefix": "Problème 7", "kind": "exercise_heading"}]
    valid, dropped = validate_page_classification(_PAGE, items, page=1)
    assert valid == []
    assert dropped == 1


def test_unknown_kind_is_dropped():
    items = [{"line_no": 1, "prefix": "Exercice 1", "kind": "banana"}]
    valid, dropped = validate_page_classification(_PAGE, items, page=1)
    assert valid == []
    assert dropped == 1


# --- Step 2.3: rolling context (cross-page classification) -------------------
# Per-page calls are cheap and stable, but "is this a heading or a sub-question"
# needs document flow. Code feeds each page the last exercise/section heading seen
# so far plus the previous page's tail, so a "(1)." opening a page is classified
# as a subquestion of the ongoing exercise, not a new heading.

def test_rolling_context_is_empty_for_the_first_page():
    assert build_rolling_context(
        last_exercise_heading=None, last_section_heading=None, prev_page_text=None
    ) == ""


def test_rolling_context_carries_the_current_exercise_and_section():
    ctx = build_rolling_context(
        last_exercise_heading="Exercice 3",
        last_section_heading="II - Numération",
        prev_page_text=None,
    )
    assert "Exercice 3" in ctx
    assert "II - Numération" in ctx


def test_rolling_context_includes_the_previous_page_tail():
    prev = "line a\nline b\nline c\nline d\nline e"
    ctx = build_rolling_context(
        last_exercise_heading="Exercice 3",
        last_section_heading=None,
        prev_page_text=prev,
    )
    # Only the last few lines carry over (not the whole page).
    assert "line e" in ctx and "line d" in ctx
    assert "line a" not in ctx


# --- Step 2 driver: serial per-page classification with rolling context ------

async def test_classify_document_collects_lines_across_pages():
    pages = ["Exercice 1\nÉcrire une fonction.", "Exercice 2\nTrier une liste."]
    fn = ScriptedClassifier([
        [{"line_no": 1, "prefix": "Exercice 1", "kind": "exercise_heading"}],
        [{"line_no": 1, "prefix": "Exercice 2", "kind": "exercise_heading"}],
    ])
    lines, report = await classify_document(pages, fn)
    assert report["dropped_invalid_lines"] == 0
    assert [(l.page, l.line_no, l.text) for l in lines] == [
        (1, 1, "Exercice 1"),
        (2, 1, "Exercice 2"),
    ]


async def test_classify_document_feeds_rolling_context_to_later_pages():
    pages = ["Exercice 3\nblah", "(1). première sous-question"]
    fn = ScriptedClassifier([
        [{"line_no": 1, "prefix": "Exercice 3", "kind": "exercise_heading"}],
        [{"line_no": 1, "prefix": "(1).", "kind": "subquestion"}],
    ])
    await classify_document(pages, fn)
    # The 2nd page's context must mention the exercise seen on page 1.
    second_context = fn.calls[1][1]
    assert "Exercice 3" in second_context


async def test_classify_document_retries_a_failing_page_once():
    pages = ["Exercice 1\nblah"]
    fn = ScriptedClassifier([
        RuntimeError("transient"),
        [{"line_no": 1, "prefix": "Exercice 1", "kind": "exercise_heading"}],
    ])
    lines, report = await classify_document(pages, fn)
    assert len(lines) == 1
    assert len(fn.calls) == 2  # initial + one retry


async def test_classify_document_raises_when_a_page_keeps_failing():
    pages = ["Exercice 1\nblah"]
    fn = ScriptedClassifier([RuntimeError("a"), RuntimeError("b")])
    with pytest.raises(RuntimeError):
        await classify_document(pages, fn)


# --- Step 3: document-level reconcile (deterministic boundary selection) ------

def _line(page, line_no, kind, text):
    return StructuralLine(page=page, line_no=line_no, kind=kind, text=text)


def test_exercise_headings_win_over_section_headings():
    # Rule 1: with any exercise_heading, only exercise_headings are boundaries.
    lines = [
        _line(1, 1, "section_heading", "Partie 1"),
        _line(1, 3, "exercise_heading", "Exercice 1"),
        _line(2, 1, "exercise_heading", "Exercice 2"),
    ]
    boundaries = reconcile_boundaries(lines)
    assert [b.text for b in boundaries] == ["Exercice 1", "Exercice 2"]


def test_section_headings_are_promoted_when_no_exercise_heading():
    # Rule 1: no exercise_heading → section_headings become the boundaries (TD 1: I/II/III).
    lines = [
        _line(1, 1, "section_heading", "I - Codage"),
        _line(1, 8, "section_heading", "II - Numération"),
        _line(2, 1, "section_heading", "III - Base 2 et base 16"),
    ]
    boundaries = reconcile_boundaries(lines)
    assert [b.text for b in boundaries] == [
        "I - Codage", "II - Numération", "III - Base 2 et base 16",
    ]


def test_toc_and_subquestions_are_never_boundaries():
    # Rule 2 + nesting: toc_entry / subquestion never split.
    lines = [
        _line(1, 1, "toc_entry", "Contenu"),
        _line(2, 1, "exercise_heading", "Exercice 1"),
        _line(2, 5, "subquestion", "(1)."),
        _line(2, 9, "subquestion", "(2)."),
    ]
    boundaries = reconcile_boundaries(lines)
    assert [b.text for b in boundaries] == ["Exercice 1"]


def test_no_printed_label_means_no_split():
    # Rule 0: no exercise_heading and no section_heading → don't split.
    lines = [
        _line(1, 1, "toc_entry", "Contenu"),
        _line(2, 3, "subquestion", "(1)."),
    ]
    assert reconcile_boundaries(lines) == []


def test_boundaries_are_returned_in_document_order():
    lines = [
        _line(2, 1, "exercise_heading", "Exercice 2"),
        _line(1, 3, "exercise_heading", "Exercice 1"),
    ]
    boundaries = reconcile_boundaries(lines)
    assert [b.text for b in boundaries] == ["Exercice 1", "Exercice 2"]


# --- Step 3 rule 4: deterministic split into exercise statements --------------

def test_split_slices_one_chunk_per_boundary_on_a_single_page():
    pages = ["Exercice 1\nÉcrire une fonction.\nExercice 2\nTrier une liste."]
    boundaries = [
        _line(1, 1, "exercise_heading", "Exercice 1"),
        _line(1, 3, "exercise_heading", "Exercice 2"),
    ]
    chunks = split_into_exercises(pages, boundaries)
    assert chunks == [
        Chunk(content="Exercice 1\nÉcrire une fonction.", page_no=1, section="Exercice 1"),
        Chunk(content="Exercice 2\nTrier une liste.", page_no=1, section="Exercice 2"),
    ]


def test_split_lets_a_statement_span_a_page_break():
    pages = ["Exercice 1\ndébut", "suite\nExercice 2\nfin"]
    boundaries = [
        _line(1, 1, "exercise_heading", "Exercice 1"),
        _line(2, 2, "exercise_heading", "Exercice 2"),
    ]
    chunks = split_into_exercises(pages, boundaries)
    # Exercise 1 absorbs the unbounded "suite" line at the top of page 2.
    assert chunks[0].content == "Exercice 1\ndébut\nsuite"
    assert chunks[0].page_no == 1
    assert chunks[1].content == "Exercice 2\nfin"
    assert chunks[1].page_no == 2


def test_split_drops_text_before_the_first_boundary():
    pages = ["Couverture du TD\nExercice 1\ncorps"]
    boundaries = [_line(1, 2, "exercise_heading", "Exercice 1")]
    chunks = split_into_exercises(pages, boundaries)
    assert len(chunks) == 1
    assert chunks[0].content == "Exercice 1\ncorps"
    assert "Couverture" not in chunks[0].content


# --- Step 3 orchestrator: classify → reconcile → split -----------------------

async def test_segment_exercises_end_to_end_with_headings():
    pages = ["Exercice 1\ndébut", "Exercice 2\nfin"]
    fn = ScriptedClassifier([
        [{"line_no": 1, "prefix": "Exercice 1", "kind": "exercise_heading"}],
        [{"line_no": 1, "prefix": "Exercice 2", "kind": "exercise_heading"}],
    ])
    chunks, report = await segment_exercises(pages, fn)
    assert [c.section for c in chunks] == ["Exercice 1", "Exercice 2"]
    assert report["segmenter"] == "llm"
    assert report["boundary_count"] == 2
    assert report["dropped_invalid_lines"] == 0


async def test_segment_exercises_rule_0_keeps_whole_doc_as_one_exercise():
    # No exercise/section heading anywhere → don't split; whole doc, no label.
    pages = ["Un énoncé continu.", "(1). sous-partie\n(2). autre"]
    fn = ScriptedClassifier([
        [],
        [{"line_no": 1, "prefix": "(1).", "kind": "subquestion"}],
    ])
    chunks, report = await segment_exercises(pages, fn)
    assert report["boundary_count"] == 0
    assert len(chunks) == 1
    assert chunks[0].section is None
    assert "Un énoncé continu." in chunks[0].content
    assert "(2). autre" in chunks[0].content
