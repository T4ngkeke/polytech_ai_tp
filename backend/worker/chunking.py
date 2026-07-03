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

from backend.app.agent.exercise_number import normalize_exercise_number
from backend.app.models import DocType

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
    numbers = [
        n for n in (normalize_exercise_number(label) for label in labels if label)
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

    if doc_type in (DocType.TD, DocType.TP):
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


async def resegment_with_llm(pages: list[str], llm_fn) -> list[Chunk]:
    """[v7.3] LLM re-judges exercise boundaries after a numbering anomaly.

    The LLM's only job is to name the TRUE boundary heading lines (verbatim);
    splitting stays deterministic. Hallucinated anchors (not found in the text)
    are ignored. An empty result means "no better segmentation" — the caller
    keeps the regex segmentation.
    """
    kept = [(i + 1, text) for i, text in enumerate(pages) if not _is_toc(text)]
    if not kept:
        return []
    joined, page_at = _join_pages(kept)

    anchors = await llm_fn(joined)
    boundaries: list[tuple[int, str]] = []
    for anchor in anchors:
        anchor = (anchor or "").strip()
        if not anchor:
            continue
        offset = joined.find(anchor)
        if offset == -1:
            continue  # hallucinated anchor — ignore
        boundaries.append((offset, anchor))

    boundaries.sort()
    return _split_at(joined, page_at, boundaries)
