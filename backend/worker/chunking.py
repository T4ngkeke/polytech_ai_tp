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

from backend.app.models import DocType

# A dotted leader followed by a page number — the signature of a TOC line.
_TOC_LINE_RE = re.compile(r"\.{3,}\s*\d+\s*$")
_TOC_HEADING_RE = re.compile(
    r"\b(table des mati|sommaire|contents|table of contents)", re.IGNORECASE
)

# Start-of-line exercise label (EN/FR), e.g. "Exercice 2", "Exercise 3:", "Problème 1".
_EXERCISE_BOUNDARY_RE = re.compile(
    r"(?im)^[ \t]*((?:exercice|exercise|exo|probl[eè]me|problem)\s*#?\s*\d+[.:)]?)"
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


def _chunk_sections(page_no: int, text: str) -> list[Chunk]:
    """Split a CM page into paragraph chunks (blank-line separated)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [Chunk(content=p, page_no=page_no, section=None) for p in paragraphs]


def _chunk_exercises(kept: list[tuple[int, str]]) -> list[Chunk]:
    """Split TD/TP material on exercise boundaries — never cut an exercise in half.

    Pages are joined first so an exercise spanning a page break stays whole; each
    chunk's page is the page where its boundary starts.
    """
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

    matches = list(_EXERCISE_BOUNDARY_RE.finditer(joined))
    if not matches:
        return [c for page_no, text in kept for c in _chunk_sections(page_no, text)]

    chunks: list[Chunk] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(joined)
        content = joined[start:end].strip()
        chunks.append(
            Chunk(content=content, page_no=page_at(start), section=match.group(1).strip())
        )
    return chunks


def chunk_pages(pages: list[str], doc_type: DocType) -> list[Chunk]:
    """Chunk parsed pages according to the document type."""
    kept = [(i + 1, text) for i, text in enumerate(pages) if not _is_toc(text)]

    if doc_type in (DocType.TD, DocType.TP):
        return _chunk_exercises(kept)

    chunks: list[Chunk] = []
    for page_no, text in kept:
        chunks.extend(_chunk_sections(page_no, text))
    return chunks
