"""
chunking.py — [v7.1] structure-aware chunking.

Splits parsed pages into retrieval chunks while honoring structure:
  * drop the table of contents (it otherwise matches every query),
  * for TD/TP, split on exercise boundaries and never cut an exercise in half,
  * keep code blocks intact,
  * carry each chunk's source page and section heading.

Pure logic over page strings; the worker feeds it pymupdf output.
"""

import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from backend.app.agent.exercise_number import (
    is_document_label,
    normalize_exercise_number,
    normalize_heading_number,
)
from backend.app.models import DocType

# [v8.0] The per-page line classifier: (line-numbered page text, rolling context)
# → a list of {"line_no", "prefix", "kind"} dicts. Injected so the segmenter is
# unit-testable without a live model (production closes over the INGEST_MODEL).
ClassifyLinesFn = Callable[[str, str], Awaitable[list[dict]]]

# [v8.0 Step 6] Bumped whenever anything that changes line numbering or boundary
# semantics changes (e.g. parsing's sort=True, the taxonomy, reconcile rules) —
# so a stale segmentation cache keyed on the old line order is invalidated.
# v8.0-2: reconcile drops document-title labels (TD n / TP n) from boundaries.
EXTRACTOR_VERSION = "v8.0-2"

# A dotted leader followed by a page number — the signature of a TOC line.
_TOC_LINE_RE = re.compile(r"\.{3,}\s*\d+\s*$")
_TOC_HEADING_RE = re.compile(
    r"\b(table des mati|sommaire|contents|table of contents)", re.IGNORECASE
)

# Start-of-line exercise label (EN/FR). [v7.3] Four heading shapes:
#   keyword + arabic   "Exercice 2", "Question 3:"
#   keyword + Roman    "Exercice IV" (uppercase only — avoids prose words)
#   Q-shorthand        "Q2.", "Q 3"
#   bare numbered      "3. Écrire une fonction…" (digit + . or ) + text)
# Bare numbering can also match sub-questions inside one exercise — that
# over-split is caught downstream by numbering-anomaly detection.
_KEYWORD = r"(?:exercice|exercise|exo|probl[eè]me|problem|question)"
_EXERCISE_BOUNDARY_RE = re.compile(
    rf"(?im)^[ \t]*("
    rf"{_KEYWORD}\s*#?\s*\d+[.:)]?"
    rf"|{_KEYWORD}\s+(?-i:[IVX]{{1,7}})\b[.:)]?"
    rf"|q\s*\d+[.:)]?"
    rf"|\d{{1,2}}[.)](?=\s+\S)"
    rf")"
)


@dataclass(frozen=True)
class Chunk:
    """A retrieval chunk with provenance."""
    content: str
    page_no: int | None
    section: str | None


def number_lines(text: str) -> str:
    """Prefix each line of a page with its 1-indexed line number ("12| ...").

    This is the deterministic substrate the LLM line-classifier references: the
    model returns a `line_no`, never verbatim text, so there is no rewrite step
    that could silently drop a boundary. Code owns the numbering and the text."""
    return "\n".join(
        f"{i + 1}| {line}" for i, line in enumerate(text.splitlines())
    )


# [v8.0] The four structural roles the per-page LLM classifier may assign a line.
# No letter-ordinal role — see EXERCISE_SEGMENTATION_PLAN "最小可用".
_STRUCTURAL_KINDS = {
    "exercise_heading",   # a top-level exercise title ("Exercice 3", "Problème 2")
    "section_heading",    # a part title ("I - Codage") — promoted to a boundary
                          # only when the document has no exercise_heading
    "subquestion",        # a sub-item inside an exercise ("(1).", "a)", "1.a")
    "toc_entry",          # a table-of-contents / Contenu line — never a boundary
}


@dataclass(frozen=True)
class StructuralLine:
    """A validated structural line: the model's `kind` re-anchored to the real
    printed text at a code-owned (page, line_no)."""
    page: int
    line_no: int
    kind: str
    text: str


def _folded(s: str) -> str:
    """Case-fold + collapse whitespace for tolerant prefix matching."""
    return " ".join(s.split()).casefold()


_ROLLING_TAIL_LINES = 3


def build_rolling_context(
    *,
    last_exercise_heading: str | None,
    last_section_heading: str | None,
    prev_page_text: str | None,
) -> str:
    """Assemble the per-page classifier's rolling context (Step 2.3).

    Carries the most recent exercise/section heading seen so far plus the tail of
    the previous page, so a line opening a page (e.g. "(1).") is judged against the
    ongoing exercise rather than as a fresh heading. Empty on the first page."""
    parts: list[str] = []
    if last_exercise_heading:
        parts.append(f"Current exercise: {last_exercise_heading}")
    if last_section_heading:
        parts.append(f"Current section: {last_section_heading}")
    if prev_page_text:
        tail = [ln for ln in prev_page_text.splitlines() if ln.strip()][-_ROLLING_TAIL_LINES:]
        if tail:
            parts.append("Previous page ended with:\n" + "\n".join(tail))
    return "\n".join(parts)


def validate_page_classification(
    page_text: str,
    raw_items: list[dict],
    *,
    page: int,
) -> tuple[list[StructuralLine], int]:
    """Re-anchor the model's per-page classification against the printed text.

    For each returned item, re-read line `line_no` and confirm it starts with the
    reported `prefix` (whitespace-folded, case-insensitive); a ±1 off-by-one is
    corrected. An out-of-range line, an unmatched prefix, or an unknown kind is
    dropped. Returns the surviving lines and the dropped count (→ ingest_report).
    """
    lines = page_text.splitlines()
    valid: list[StructuralLine] = []
    dropped = 0
    for item in raw_items:
        line_no = item.get("line_no")
        kind = item.get("kind")
        prefix = _folded(item.get("prefix") or "")
        if not isinstance(line_no, int) or isinstance(line_no, bool):
            dropped += 1
            continue
        if kind not in _STRUCTURAL_KINDS or not prefix:
            dropped += 1
            continue
        # Search the reported line first, then ±1 (off-by-one correction).
        corrected = None
        for candidate in (line_no, line_no + 1, line_no - 1):
            if 1 <= candidate <= len(lines) and _folded(lines[candidate - 1]).startswith(prefix):
                corrected = candidate
                break
        if corrected is None:
            dropped += 1
            continue
        valid.append(
            StructuralLine(page=page, line_no=corrected, kind=kind, text=lines[corrected - 1])
        )
    return valid, dropped


async def _classify_page_with_retry(
    classify_fn: ClassifyLinesFn,
    numbered: str,
    context: str,
    retries: int,
) -> list[dict]:
    """Call the classifier for one page, retrying transient failures. Re-raises
    after the last attempt so the caller can fall back to regex (Step 4)."""
    for attempt in range(retries + 1):
        try:
            return await classify_fn(numbered, context)
        except Exception:
            if attempt == retries:
                raise
    return []  # unreachable


async def classify_document(
    pages: list[str],
    classify_fn: ClassifyLinesFn,
    *,
    retries: int = 1,
) -> tuple[list[StructuralLine], dict]:
    """Classify a document's structural lines page-by-page (Step 2 driver).

    Calls run **serially** because each page's rolling context depends on the
    headings seen on earlier pages. Each page's result is validated/re-anchored
    against the printed text (Step 2.4). A page that keeps failing after retries
    raises — the caller falls back to the regex segmenter (Step 4). Returns all
    validated structural lines and a report (`dropped_invalid_lines`)."""
    all_lines: list[StructuralLine] = []
    dropped_total = 0
    last_exercise_heading: str | None = None
    last_section_heading: str | None = None
    prev_page_text: str | None = None

    for index, text in enumerate(pages):
        context = build_rolling_context(
            last_exercise_heading=last_exercise_heading,
            last_section_heading=last_section_heading,
            prev_page_text=prev_page_text,
        )
        raw_items = await _classify_page_with_retry(
            classify_fn, number_lines(text), context, retries
        )
        valid, dropped = validate_page_classification(text, raw_items, page=index + 1)
        all_lines.extend(valid)
        dropped_total += dropped
        for line in valid:
            if line.kind == "exercise_heading":
                last_exercise_heading = line.text
            elif line.kind == "section_heading":
                last_section_heading = line.text
        prev_page_text = text

    return all_lines, {"dropped_invalid_lines": dropped_total}


def reconcile_boundaries(lines: list[StructuralLine]) -> list[StructuralLine]:
    """Select the final exercise boundaries from all classified lines (Step 3).

    Deterministic, no LLM. Rules:
      0. No exercise_heading AND no section_heading (no printed label to copy) →
         return [] — the caller keeps the whole document as one exercise
         (number_normalized=None). We never fabricate a boundary from position.
      1. Nesting: with ≥1 exercise_heading, only exercise_headings are boundaries
         (section_headings don't split); with none, section_headings are promoted.
      2. toc_entry / subquestion are never boundaries (they aren't in either set).
    Returned in document order (page, line_no)."""
    # Drop any heading that is really the document title ("TD 1 - ..."): the
    # classifier sometimes labels it a heading, and a single spurious
    # exercise_heading would otherwise suppress every real section (nesting rule).
    lines = [l for l in lines if not is_document_label(l.text)]
    exercise_headings = [l for l in lines if l.kind == "exercise_heading"]
    section_headings = [l for l in lines if l.kind == "section_heading"]
    if exercise_headings:
        boundaries = exercise_headings
    elif section_headings:
        boundaries = section_headings
    else:
        return []  # rule 0 — no printed label → don't split
    return sorted(boundaries, key=lambda l: (l.page, l.line_no))


def split_into_exercises(
    pages: list[str], boundaries: list[StructuralLine]
) -> list[Chunk]:
    """Slice the document into one statement per boundary (Step 3 rule 4).

    Between adjacent boundaries, every line (across page breaks) belongs to the
    earlier exercise; text before the first boundary is dropped (cover / intro /
    TOC); a page with no boundary folds into the previous exercise. Each Chunk's
    `section` is the boundary heading text (the printed label) and `content` is
    the statement body (heading line included)."""
    flat: list[tuple[int, int, str]] = [
        (page_no, line_no, line)
        for page_no, text in enumerate(pages, 1)
        for line_no, line in enumerate(text.splitlines(), 1)
    ]
    index_of = {(p, ln): i for i, (p, ln, _) in enumerate(flat)}
    starts = [index_of[(b.page, b.line_no)] for b in boundaries]

    chunks: list[Chunk] = []
    for i, start_idx in enumerate(starts):
        end_idx = starts[i + 1] if i + 1 < len(starts) else len(flat)
        body = "\n".join(flat[j][2] for j in range(start_idx, end_idx)).strip()
        chunks.append(
            Chunk(content=body, page_no=boundaries[i].page, section=boundaries[i].text)
        )
    return chunks


async def segment_exercises(
    pages: list[str],
    classify_fn: ClassifyLinesFn,
    *,
    retries: int = 1,
) -> tuple[list[Chunk], dict]:
    """LLM-primary exercise segmenter (Steps 2+3 orchestrated).

    classify each page's structural lines → reconcile to boundaries → split into
    statements. With no printed label anywhere (rule 0) the whole document is kept
    as a single label-less exercise (section=None → number_normalized=None), never
    force-split. Returns the statement chunks and a report (`segmenter`,
    `boundary_count`, `dropped_invalid_lines`). Raising propagates to the caller,
    which falls back to the regex segmenter (Step 4)."""
    lines, report = await classify_document(pages, classify_fn, retries=retries)
    boundaries = reconcile_boundaries(lines)
    report["segmenter"] = "llm"
    report["boundary_count"] = len(boundaries)

    if not boundaries:
        whole = "\n".join(pages).strip()
        chunks = [Chunk(content=whole, page_no=1, section=None)] if whole else []
        return chunks, report

    return split_into_exercises(pages, boundaries), report


def _is_toc(text: str) -> bool:
    """Heuristic: a page dominated by dotted-leader entries is a table of contents."""
    leader_lines = sum(1 for line in text.splitlines() if _TOC_LINE_RE.search(line))
    if leader_lines >= 2:
        return True
    return bool(_TOC_HEADING_RE.search(text) and leader_lines >= 1)


# [v7.3] CM chunk size control: below `min`, neighbouring paragraphs merge
# (3-word headers make noise embeddings); above `max`, split at sentence
# boundaries (a dense page dilutes its embedding).
MIN_CHUNK_CHARS = 120
MAX_CHUNK_CHARS = 1600
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _paragraphs_code_aware(text: str) -> list[str]:
    """Split a page into paragraphs on blank lines — except blank lines inside
    an indented code block (next non-blank line indented), which stay put so
    code is never cut in half."""
    lines = text.splitlines()
    paragraphs: list[str] = []
    current: list[str] = []
    for idx, line in enumerate(lines):
        if line.strip():
            current.append(line)
            continue
        following = next((l for l in lines[idx + 1:] if l.strip()), None)
        if current and following is not None and following.startswith(("    ", "\t")):
            current.append(line)  # blank line inside a code block
        elif current:
            paragraphs.append("\n".join(current).strip("\n"))
            current = []
    if current:
        paragraphs.append("\n".join(current).strip("\n"))
    return [p for p in paragraphs if p.strip()]


def _merge_small(paragraphs: list[str], min_chars: int) -> list[str]:
    """Merge adjacent paragraphs until each unit reaches min_chars."""
    merged: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(buffer) >= min_chars:
            merged.append(buffer)
            buffer = ""
    if buffer:
        if merged:
            merged[-1] = f"{merged[-1]}\n\n{buffer}"
        else:
            merged.append(buffer)
    return merged


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Split an oversized unit at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current:
        pieces.append(current)
    return pieces


def _chunk_sections(
    page_no: int,
    text: str,
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """Split a CM page into size-controlled, code-aware paragraph chunks."""
    chunks: list[Chunk] = []
    for unit in _merge_small(_paragraphs_code_aware(text), min_chars):
        for piece in _split_oversized(unit, max_chars):
            chunks.append(Chunk(content=piece, page_no=page_no, section=None))
    return chunks


def _join_pages(kept: list[tuple[int, str]]):
    """Join pages into one text (so exercises spanning a page break stay whole)
    and return it with an offset→page resolver."""
    joined = ""
    starts: list[tuple[int, int]] = []  # (char_offset, page_no)
    for page_no, text in kept:
        starts.append((len(joined), page_no))
        joined += text + "\n"

    def page_at(offset: int) -> int:
        page_no = starts[0][1]
        for start_offset, candidate in starts:
            if start_offset <= offset:
                page_no = candidate
            else:
                break
        return page_no

    return joined, page_at


def _split_at(joined: str, page_at, boundaries: list[tuple[int, str]]) -> list[Chunk]:
    """Cut the joined text at (offset, label) boundaries — one chunk per boundary."""
    chunks: list[Chunk] = []
    for i, (start, label) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(joined)
        chunks.append(Chunk(
            content=joined[start:end].strip(),
            page_no=page_at(start),
            section=label,
        ))
    return chunks


def _chunk_exercises(kept: list[tuple[int, str]]) -> list[Chunk]:
    """Split TD/TP material on exercise boundaries — never cut an exercise in half."""
    joined, page_at = _join_pages(kept)

    matches = list(_EXERCISE_BOUNDARY_RE.finditer(joined))
    if not matches:
        return [c for page_no, text in kept for c in _chunk_sections(page_no, text)]

    boundaries = [(m.start(), m.group(1).strip()) for m in matches]
    return _split_at(joined, page_at, boundaries)


def detect_numbering_anomaly(labels: list[str | None]) -> str | None:
    """[v7.3] Judge a segmentation's numbering health.

    Bare-number boundaries can mistake sub-questions for exercises ("Exercice 1"
    + sub-items 1/2/3 → numbers [1, 1, 2, 3]). Returns:
      * "duplicate_numbers" — over-split; caller re-segments with an LLM,
      * "number_gap"        — a boundary was likely missed; report-only,
      * None                — numbering looks sane (or nothing to judge).
    """
    # Labels are heading LINES — read the ordinal token, not a digit in the title
    # (so "III - Base 2 et base 16" is 3, not a false duplicate at 2).
    numbers = [
        n for n in (normalize_heading_number(label) for label in labels if label)
        if n is not None
    ]
    if len(numbers) < 2:
        return None
    if len(set(numbers)) < len(numbers):
        return "duplicate_numbers"
    ordered = sorted(set(numbers))
    if any(b - a > 1 for a, b in zip(ordered, ordered[1:])):
        return "number_gap"
    return None


def chunk_pages(
    pages: list[str],
    doc_type: DocType,
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """Chunk parsed pages according to the document type.

    CM: code-aware paragraph split → document-level paragraph dedup (recurring
    notices) → merge tiny neighbours → sentence-split oversized units.
    TD/TP: exercise-boundary segmentation (sizes don't apply — an exercise is
    never size-split).
    """
    kept = [(i + 1, text) for i, text in enumerate(pages) if not _is_toc(text)]

    # [v8.0] A corrigé is numbered like a TD/TP (answers per exercise number),
    # so it segments on the same boundaries — the bodies become Answer rows.
    if doc_type in (DocType.TD, DocType.TP, DocType.corrige):
        return _chunk_exercises(kept)

    chunks: list[Chunk] = []
    seen_paragraphs: set[str] = set()
    for page_no, text in kept:
        fresh: list[str] = []
        for paragraph in _paragraphs_code_aware(text):
            key = paragraph.strip()
            if key in seen_paragraphs:
                continue
            seen_paragraphs.add(key)
            fresh.append(paragraph)
        for unit in _merge_small(fresh, min_chars):
            for piece in _split_oversized(unit, max_chars):
                chunks.append(Chunk(content=piece, page_no=page_no, section=None))
    return chunks
