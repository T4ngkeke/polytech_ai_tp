"""
vlm.py — [v8.1 item 8] garbled-formula detection + VLM transcription repair.

PDFs printed from HTML mangle math (superscripts flatten: 2^15 → "215";
Unicode math alphabets survive as span soup). Detection is DETERMINISTIC
(spike-validated 2026-07-17): per-line word-char ratio, math-symbol density
and mojibake — with the spike's two false-positive fixes (indented code lines
and dot-leader junk are never flagged). Adjacent flagged lines merge into one
region: the crop we render and send to the vision model.

Red lines:
  * The VLM TRANSCRIBES what is printed — it never invents. A transcription
    that introduces a new exercise boundary is refused outright.
  * Every region lands in an audit entry (before/after) for the teacher.
  * No failure ever breaks ingestion — a bad region keeps its original text.

Runs on the off-peak worker only, and only when VLM_MODEL is configured.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Awaitable, Callable

from backend.worker.chunking import _EXERCISE_BOUNDARY_RE

# Spike-tuned thresholds (scripts/vlm_detect_spike.py, run on the real corpus).
WORD_RATIO_MIN = 0.55
MATH_COUNT_MIN = 3
MATH_RATIO_MIN = 0.15
MIN_LINE_CHARS = 4

# render_fn(page_index, region_lines) -> PNG bytes (production closes over the
# PDF path); transcribe_fn(png_bytes) -> the VLM's transcription.
RenderFn = Callable[[int, list[str]], bytes]
TranscribeFn = Callable[[bytes], Awaitable[str]]


@dataclass(frozen=True)
class Region:
    """A run of adjacent suspicious lines on one page (1-based line numbers)."""
    page_index: int
    line_start: int
    line_end: int
    lines: list[str]


def _non_space(line: str) -> list[str]:
    return [c for c in line if not c.isspace()]


def _word_char_ratio(line: str) -> float:
    chars = _non_space(line)
    if not chars:
        return 1.0
    word = sum(1 for c in chars if unicodedata.category(c)[0] in ("L", "N"))
    return word / len(chars)


def _is_mathy(c: str) -> bool:
    if ord(c) < 128:
        return False
    if unicodedata.category(c) in ("Sm", "So"):
        return True
    # Unicode math alphanumerics (𝑎, 𝑁 …) and letterlike symbols (ℕ, ℝ …).
    return 0x1D400 <= ord(c) <= 0x1D7FF or 0x2100 <= ord(c) <= 0x214F


def _is_code_line(line: str) -> bool:
    """Spike fix: indented lines are code — fine as text, never sent to vision."""
    return line.startswith(("    ", "\t"))


def _is_dot_leader(line: str) -> bool:
    """Spike fix: '... ...' / '……' junk lines — noise, not mangled math."""
    return set(line.strip()) <= {".", "…", " "}


def _line_is_garbled(line: str) -> bool:
    if len(line.strip()) < MIN_LINE_CHARS or _is_code_line(line) or _is_dot_leader(line):
        return False
    if any(c == "�" or 0xE000 <= ord(c) <= 0xF8FF for c in line):
        return True  # mojibake / private-use chars
    chars = _non_space(line)
    mathy = sum(1 for c in chars if _is_mathy(c))
    if mathy >= MATH_COUNT_MIN and mathy / len(chars) >= MATH_RATIO_MIN:
        return True
    return _word_char_ratio(line) < WORD_RATIO_MIN


def find_garbled_regions(pages: list[str]) -> list[Region]:
    """Deterministic detection: suspicious lines, adjacent ones merged."""
    regions: list[Region] = []
    for page_index, page in enumerate(pages):
        current: list[tuple[int, str]] = []
        for line_no, line in enumerate(page.splitlines(), 1):
            if _line_is_garbled(line):
                current.append((line_no, line))
            elif current:
                regions.append(Region(
                    page_index, current[0][0], current[-1][0],
                    [text for _, text in current],
                ))
                current = []
        if current:
            regions.append(Region(
                page_index, current[0][0], current[-1][0],
                [text for _, text in current],
            ))
    return regions


_UNREADABLE_MARKERS = ("[illisible]", "[unreadable]")


def _introduces_boundary(original_lines: list[str], transcription: str) -> bool:
    """True when the transcription contains an exercise boundary the original
    region did not — the VLM inventing structure, which we refuse."""
    original_has = any(_EXERCISE_BOUNDARY_RE.match(l) for l in original_lines)
    if original_has:
        return False
    return any(
        _EXERCISE_BOUNDARY_RE.match(line) for line in transcription.splitlines()
    )


async def repair_pages(
    pages: list[str],
    *,
    render_fn: RenderFn,
    transcribe_fn: TranscribeFn,
) -> tuple[list[str], list[dict]]:
    """Repair every garbled region; return (new pages, audit entries).

    Per region: render → transcribe → safety checks → splice the transcription
    over the original lines. Any refusal/failure keeps the original text; a
    raise from render/transcribe marks the region failed and moves on."""
    regions = find_garbled_regions(pages)
    if not regions:
        return pages, []

    entries: list[dict] = []
    # Apply per page, bottom-up, so earlier line numbers stay valid.
    page_lines = [p.splitlines() for p in pages]
    for region in sorted(regions, key=lambda r: (r.page_index, -r.line_start)):
        before = "\n".join(region.lines)
        entry = {
            "page": region.page_index + 1,
            "line_start": region.line_start,
            "line_end": region.line_end,
            "before": before,
        }
        try:
            png = render_fn(region.page_index, region.lines)
            transcription = (await transcribe_fn(png)).strip()
        except Exception as exc:
            entries.append({**entry, "status": "failed", "error": str(exc)})
            continue

        low = transcription.lower()
        if not transcription or any(m in low for m in _UNREADABLE_MARKERS):
            entries.append({**entry, "status": "unreadable"})
            continue
        if _introduces_boundary(region.lines, transcription):
            # Red line: transcribe-only — never let the VLM invent an exercise.
            entries.append({**entry, "status": "refused_new_boundary",
                            "after": transcription})
            continue

        lines = page_lines[region.page_index]
        lines[region.line_start - 1: region.line_end] = transcription.splitlines()
        entries.append({**entry, "status": "replaced", "after": transcription})

    entries.reverse()  # document order for the teacher
    return ["\n".join(lines) for lines in page_lines], entries


def render_region(pdf_path: str, page_index: int, lines: list[str], *,
                  dpi: int = 150, margin: float = 6.0) -> bytes:
    """Crop the region's area from the PDF page and render it as PNG bytes.

    Line bboxes come from pymupdf's dict extraction, matched back by folded
    text. When no line matches (spacing drift), the whole page is rendered —
    still a bounded image, and the VLM prompt asks for a transcription of
    what is visible."""
    import fitz  # lazy, like parsing.extract_pdf_text

    def fold(s: str) -> str:
        return " ".join(s.split()).casefold()

    wanted = {fold(l) for l in lines if l.strip()}
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        rects = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line.get("spans", []))
                if fold(text) in wanted:
                    rects.append(fitz.Rect(line["bbox"]))
        if rects:
            clip = rects[0]
            for r in rects[1:]:
                clip |= r
            clip = fitz.Rect(clip.x0 - margin, clip.y0 - margin,
                             clip.x1 + margin, clip.y1 + margin) & page.rect
        else:
            clip = page.rect
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        return pix.tobytes("png")


_TRANSCRIBE_PROMPT = (
    "Transcribe EXACTLY the text and mathematical formulas visible in this "
    "image, in reading order. Write mathematics as LaTeX between $…$. Do not "
    "add, complete, correct or invent anything that is not printed. If a part "
    "is unreadable, write [illisible] for that part only."
)


def make_transcribe_fn(client, model: str) -> TranscribeFn:
    """Production TranscribeFn: one multimodal chat call on the VLM slot."""
    import base64

    async def transcribe(png: bytes) -> str:
        data_url = "data:image/png;base64," + base64.b64encode(png).decode()
        resp = await client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _TRANSCRIBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        )
        return resp.choices[0].message.content or ""

    return transcribe
