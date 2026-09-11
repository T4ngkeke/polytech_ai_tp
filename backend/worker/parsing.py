"""
parsing.py — [v7.1] PDF text extraction + character-yield input gate.

Input is PDF only (instructors export slides to PDF before upload). Extraction is
pymupdf; there is no OCR. A cheap character-yield gate rejects scanned / garbled
PDFs back to the teacher (`needs_review`) rather than silently ingesting garbage.
"""

import re
from collections import Counter
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# A digital-born course PDF yields ~600-2100 chars/page; a scanned/image PDF yields
# almost none. Stay well below the clean floor so real material is never rejected.
MIN_AVG_CHARS_PER_PAGE = 80
# Fraction of non-whitespace characters that must be word characters (letters or
# digits, any script). Real prose is well above this; a garbled extraction is below.
MIN_WORD_CHAR_RATIO = 0.5


@dataclass(frozen=True)
class GateResult:
    """Outcome of the character-yield gate. `reason` is set only on rejection."""
    ok: bool
    reason: str | None = None


def extract_pdf_text(path: str | Path, *, clean_margins: bool = False) -> list[str]:
    """Extract PDF text; optionally remove repeated, geometrically proven margins.

    Keep the original sorted text extraction. Dict extraction supplies positions
    only: a line is eligible for cleaning if it occurs once in the text and all
    matching PDF lines lie entirely within the top/bottom 5% (at most 36 pt).
    Ambiguous matches are retained, including identical text in the page body.
    """
    import fitz

    pages: list[str] = []
    margins: list[set[int]] = []
    with fitz.open(str(path)) as doc:
        for page in doc:
            text = page.get_text("text", sort=True)
            pages.append(text)
            if not clean_margins:
                continue
            positions: dict[str, list[bool]] = {}
            edge = min(36, page.rect.height * 0.05)
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    key = "".join(span["text"] for span in line.get("spans", [])).strip()
                    _, y0, _, y1 = line["bbox"]
                    at_margin = y1 <= page.rect.y0 + edge or y0 >= page.rect.y1 - edge
                    positions.setdefault(key, []).append(at_margin)
            lines = text.splitlines()
            counts = Counter(line.strip() for line in lines)
            margins.append({
                i for i, line in enumerate(lines)
                if counts[line.strip()] == 1 and positions.get(line.strip())
                and all(positions[line.strip()])
            })
    return strip_repeated_lines(pages, margin_lines=margins) if clean_margins else pages


def character_yield_gate(
    pages: list[str],
    *,
    min_avg_chars_per_page: int = MIN_AVG_CHARS_PER_PAGE,
) -> GateResult:
    """Decide whether extracted page text is rich enough to ingest."""
    if not pages:
        return GateResult(ok=False, reason="No pages extracted from the PDF.")

    total_chars = sum(len(p.strip()) for p in pages)
    avg = total_chars / len(pages)
    if avg < min_avg_chars_per_page:
        return GateResult(
            ok=False,
            reason=(
                f"Low character yield ({avg:.0f} chars/page < {min_avg_chars_per_page}). "
                "The PDF looks scanned or image-only — re-export a text-based PDF."
            ),
        )

    word_ratio = _word_char_ratio("".join(pages))
    if word_ratio < MIN_WORD_CHAR_RATIO:
        return GateResult(
            ok=False,
            reason=(
                f"Low word-character ratio ({word_ratio:.0%} < {MIN_WORD_CHAR_RATIO:.0%}). "
                "The extracted text looks garbled — re-export a clean text-based PDF."
            ),
        )

    return GateResult(ok=True)


# A pure pagination line: optional page word, then digits (optionally '/total'),
# optionally dash-decorated. Deliberately narrow — 'Exercice 1' must NOT match.
_PAGINATION_RE = re.compile(
    r"(?i)^\s*(?:page|p\.?)?\s*[-–—]?\s*\d+\s*(?:/\s*\d+)?\s*[-–—]?\s*$"
)


def strip_repeated_lines(
    pages: list[str], min_repeat: int = 3, *,
    margin_lines: list[set[int]] | None = None,
) -> list[str]:
    """Clean only repeated lines with PDF margin coordinates (zero-based indices).

    Text repetition alone is not evidence of a header: code and definitions
    legitimately repeat. Without positions (Markdown, HTML or text), keep all
    lines. Replace removed lines with blanks to preserve line numbering.
    """
    if margin_lines is None:
        return list(pages)
    if len(margin_lines) != len(pages):
        raise ValueError("Margin metadata must match the number of pages.")
    pages_per_line: Counter[str] = Counter()
    pagination_pages = 0
    for page, indices in zip(pages, margin_lines):
        keys = {line.strip() for i, line in enumerate(page.splitlines()) if i in indices}
        pages_per_line.update(key for key in keys if key)
        pagination_pages += any(_PAGINATION_RE.fullmatch(key) for key in keys if key)
    repeated = {key for key, count in pages_per_line.items() if count >= min_repeat}
    strip_pagination = pagination_pages >= min_repeat
    return [
        "".join(
            "\n" if i in indices and (
                line.strip() in repeated or
                (strip_pagination and _PAGINATION_RE.fullmatch(line.strip()))
            ) else line
            for i, line in enumerate(page.splitlines(keepends=True))
        )
        for page, indices in zip(pages, margin_lines)
    ]


# [v8.1] HTML → markdown-ish text. Teachers' HTML (when they have it) is clean
# tool output, and it beats a printed PDF for one big reason: KaTeX / MathJax
# keep the ORIGINAL LaTeX inside their rendered markup, so formulas that come
# out mangled from a PDF are recovered intact here. Headings become #-lines so
# `split_markdown_pages` pages the result; <pre> is fenced so code comments
# never fake a heading; page chrome is stripped.
_MATHJAX_SCRIPT_TYPE_RE = re.compile(r"math/tex")
_CHROME_TAGS = ["script", "style", "nav", "header", "footer", "aside"]
_BLOCK_TAGS = ["p", "li", "tr", "blockquote", "table", "ul", "ol", "div"]


def html_to_markdown(html: str) -> str:
    """Convert clean course HTML to markdown-ish text (headings, fences, LaTeX)."""
    from bs4 import BeautifulSoup  # lazy import — keeps the gate import-light

    soup = BeautifulSoup(html, "html.parser")

    # 1. MathJax v2 keeps the source LaTeX in <script type="math/tex"> —
    #    recover it BEFORE chrome stripping deletes every script.
    for tag in soup.find_all("script", type=_MATHJAX_SCRIPT_TYPE_RE):
        tag.replace_with(f"${tag.get_text()}$")

    # 2. KaTeX / MathJax v3 embed it in a MathML <annotation>; replace the whole
    #    rendered widget so the visible span soup is not duplicated.
    for ann in soup.find_all("annotation"):
        if "application/x-tex" not in (ann.get("encoding") or ""):
            continue
        latex = ann.get_text()
        target = ann.find_parent(class_="katex") or ann.find_parent("math") or ann
        target.replace_with(f"${latex}$")

    # 3. Strip page chrome (menus, banners, styling, leftover scripts).
    for tag in soup.find_all(_CHROME_TAGS):
        tag.decompose()

    # 4. Structure → markdown markers, as self-contained text nodes.
    for level in range(1, 7):
        for tag in soup.find_all(f"h{level}"):
            tag.replace_with(f"\n\n{'#' * level} {tag.get_text(' ', strip=True)}\n\n")
    for tag in soup.find_all("pre"):
        code = tag.get_text()
        # A code example may itself contain Markdown fences. Use a longer outer
        # fence so its comments and blank lines remain code throughout parsing.
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", code)), default=3))
        tag.replace_with(f"\n\n{fence}\n{code}\n{fence}\n\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n\n")

    # No artificial separators: HTML without whitespace between nodes renders
    # glued, so faithful extraction glues too. Whitespace-only lines (source
    # pretty-printing) become blank lines; code indentation inside fences is
    # untouched.
    text = soup.get_text("")
    lines: list[str] = []
    fence = None
    for line in text.splitlines():
        previous_fence = fence
        fence = markdown_fence_state(line, fence)
        if previous_fence is not None or fence is not None:
            lines.append(line)
        elif line.strip():
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


# [v8.1] Markdown "pages". PDF pages are the unit every downstream step works
# on (per-page classifier context, page_no citations); a markdown file has no
# pages, so we synthesize them from its heading tree: split at the SHALLOWEST
# heading level present (a doc using only "##" still splits). Hash lines inside
# ``` / ~~~ fences (e.g. Python comments) are code, never headings.
_MD_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")


def markdown_fence_state(line: str, fence: str | None) -> str | None:
    """Track matching fence character and length; shorter/other fences are code."""
    match = _MD_FENCE_RE.match(line)
    if not match:
        return fence
    marker, suffix = match.groups()
    if fence is None:
        if marker[0] == "`" and "`" in suffix:
            return None
        return marker
    if marker[0] == fence[0] and len(marker) >= len(fence) and not suffix.strip():
        return None
    return fence


def split_markdown_pages(text: str) -> list[str]:
    """Split markdown into pseudo-pages at its shallowest heading level.

    Content before the first heading becomes its own leading page; a doc with
    no headings at all is a single page. Empty input → no pages."""
    lines = text.splitlines()
    fence = None
    headings: list[tuple[int, int]] = []  # (line index, heading level)
    for i, line in enumerate(lines):
        previous_fence = fence
        fence = markdown_fence_state(line, fence)
        if previous_fence is not None or fence is not None:
            continue
        m = _MD_HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1))))

    if not headings:
        stripped = text.strip("\r\n")
        return [stripped] if stripped.strip() else []

    min_level = min(level for _, level in headings)
    starts = [i for i, level in headings if level == min_level]

    pages: list[str] = []
    preamble = "\n".join(lines[: starts[0]]).strip("\r\n")
    if preamble.strip():
        pages.append(preamble)
    for j, start in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start:end]).strip("\r\n")
        if body.strip():
            pages.append(body)
    return pages


def _word_char_ratio(text: str) -> float:
    """Fraction of non-whitespace chars that are letters or digits (any script)."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    word_chars = sum(1 for c in non_ws if unicodedata.category(c)[0] in ("L", "N"))
    return word_chars / len(non_ws)
