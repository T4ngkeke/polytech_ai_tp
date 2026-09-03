"""
chunking.py — split parsed pages into retrieval chunks.

Honours structure where it is cheap and safe to do so:
  * drop the table of contents (it otherwise matches every query),
  * keep code blocks intact — a blank line inside an indented block is not a
    paragraph break,
  * merge paragraphs that are too small to embed meaningfully, split ones too
    large to embed precisely,
  * drop a paragraph repeated across pages (a recurring header or notice),
  * carry each chunk's source page.

There used to be a second half here: exercise-boundary segmentation, driven by a
per-page LLM line classifier with a regex cross-check, numbering-anomaly
detection and a boundary cache. It existed to build Exercises rows, and a row is
a hard claim — the system said "this is exercise 3" and served it with
confidence, so the segmentation had to be exactly right, and all that machinery
was there to make it so. With no rows to build, none of it has a job.

Pure logic over page strings; the worker feeds it pymupdf output.
"""

import re
from dataclasses import dataclass

# A dotted leader followed by a page number — the signature of a TOC line.
_TOC_LINE_RE = re.compile(r"\.{3,}\s*\d+\s*$")
_TOC_HEADING_RE = re.compile(
    r"\b(table des mati|sommaire|contents|table of contents)", re.IGNORECASE
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


# Size control: below `min`, neighbouring paragraphs merge (a 3-word header makes
# a noise embedding); above `max`, split at sentence boundaries (a dense page
# dilutes its embedding).
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


def chunk_pages(
    pages: list[str],
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """Chunk parsed pages.

    Drop TOC pages → code-aware paragraph split → document-level paragraph dedup
    (recurring notices) → merge tiny neighbours → sentence-split oversized units.
    """
    kept = [(i + 1, text) for i, text in enumerate(pages) if not _is_toc(text)]

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
