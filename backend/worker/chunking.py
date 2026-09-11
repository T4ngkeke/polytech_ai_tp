"""Split course material while preserving body text, headings and source order.

Repeated paragraphs and contents pages remain available: relevance is a search
concern. Markdown headings define sections; PDF headings are not guessed from
plain text. Fenced code stays together when it fits; long blocks split at lines.
The character limit is a hard storage bound, not a model token-count guarantee.
"""

import re
from dataclasses import dataclass

from backend.worker.parsing import markdown_fence_state


@dataclass(frozen=True)
class Chunk:
    content: str
    page_no: int | None
    section: str | None
    source_text: str | None = None
    source_ref: dict | None = None


MIN_CHUNK_CHARS = 120
MAX_CHUNK_CHARS = 1600
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
_SENTENCE_END_RE = re.compile(r"[.!?。！？](?:\s+|$)")


def _section_parts(pages: list[str], markdown: bool):
    """Yield page/heading/text without merging material across heading changes."""
    headings: list[tuple[int, str]] = []
    fence = None
    for page_no, page in enumerate(pages, 1):
        current: list[str] = []
        section = " > ".join(title for _, title in headings)[-512:] or None
        for line in page.splitlines():
            previous_fence = fence
            fence = markdown_fence_state(line, fence)
            heading = _HEADING_RE.match(line) if markdown and not previous_fence and not fence else None
            if heading:
                if current:
                    yield page_no, section, "\n".join(current)
                level = len(heading[1])
                title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2])
                headings = [(depth, name) for depth, name in headings if depth < level]
                headings.append((level, title))
                # The model column is VARCHAR(512); keep the most local headings.
                section = " > ".join(name for _, name in headings)[-512:]
                current = [line]
            else:
                current.append(line)
        if current:
            yield page_no, section, "\n".join(current)


def _paragraphs_code_aware(text: str) -> list[str]:
    lines = text.splitlines()
    paragraphs: list[str] = []
    current: list[str] = []
    fence = None

    def flush():
        if current and any(line.strip() for line in current):
            paragraphs.append("\n".join(current).strip("\n"))
        current.clear()

    for idx, line in enumerate(lines):
        next_fence = markdown_fence_state(line, fence)
        if fence is not None:
            current.append(line)
            fence = next_fence
            if fence is None:
                flush()
        elif next_fence is not None:
            flush()
            current.append(line)
            fence = next_fence
        elif line.strip():
            current.append(line)
        else:
            following = next((value for value in lines[idx + 1:] if value.strip()), None)
            if current and following is not None and following.startswith(("    ", "\t")):
                current.append(line)
            else:
                flush()
    flush()
    return paragraphs


def _is_code(text: str) -> bool:
    return markdown_fence_state(text.splitlines()[0], None) is not None or any(
        line.startswith(("    ", "\t")) for line in text.splitlines()
    )


def _merge_small(paragraphs: list[str], min_chars: int) -> list[str]:
    merged: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        # Do not glue prose onto code: oversized code must still be recognizable.
        if _is_code(paragraph):
            if buffer:
                merged.append(buffer)
                buffer = ""
            merged.append(paragraph)
            continue
        buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(buffer) >= min_chars:
            merged.append(buffer)
            buffer = ""
    if buffer:
        if merged and not _is_code(merged[-1]):
            merged[-1] = f"{merged[-1]}\n\n{buffer}"
        else:
            merged.append(buffer)
    return merged


def _split_text(text: str, limit: int, *, code: bool = False) -> list[str]:
    """Prefer structural boundaries, then whitespace, then a hard character cut.

    Do not strip/reformat fragments: joining them reconstructs the input exactly.
    In particular, preserve indentation and punctuation inside code.
    """
    pieces: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n") + 1
        if not cut and not code:
            ends = [match.end() for match in _SENTENCE_END_RE.finditer(window)]
            cut = ends[-1] if ends else 0
        if not cut and not code:
            cut = max(window.rfind(" "), window.rfind("\t")) + 1
        cut = cut or limit
        pieces.append(text[:cut])
        text = text[cut:]
    if text:
        pieces.append(text)
    return pieces


def _split_oversized(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    lines = text.splitlines(keepends=True)
    fence = markdown_fence_state(lines[0].rstrip("\r\n"), None)
    if fence:
        closed = len(lines) > 1 and markdown_fence_state(lines[-1].rstrip("\r\n"), fence) is None
        opening = lines[0].rstrip("\r\n") + "\n"
        closing = "\n" + (lines[-1].strip() if closed else fence)
        room = max_chars - len(opening) - len(closing)
        if room > 0:
            body = "".join(lines[1:-1] if closed else lines[1:])
            return [opening + part + closing for part in _split_text(body, room, code=True)]
    return _split_text(text, max_chars, code=_is_code(text))


def chunk_pages(
    pages: list[str],
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
    *,
    markdown: bool = True,
) -> list[Chunk]:
    """Keep all source occurrences, with bounded chunks in document order.

    page_no is a PDF page or the parser's text section number. It is not an
    original Markdown/HTML line number. No PDF layout reconstruction is claimed.
    """
    if not 0 < min_chars <= max_chars:
        raise ValueError("Chunk sizes must satisfy 0 < min_chars <= max_chars.")
    chunks: list[Chunk] = []
    for page_no, section, text in _section_parts(pages, markdown):
        paragraphs = _paragraphs_code_aware(text)
        for unit in _merge_small(paragraphs, min_chars):
            for piece in _split_oversized(unit, max_chars):
                if piece.strip():
                    chunks.append(Chunk(content=piece, page_no=page_no, section=section))
    return chunks
