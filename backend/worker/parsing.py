"""
parsing.py — [v7.1] PDF text extraction + character-yield input gate.

Input is PDF only (instructors export slides to PDF before upload). Extraction is
pymupdf; there is no OCR. A cheap character-yield gate rejects scanned / garbled
PDFs back to the teacher (`needs_review`) rather than silently ingesting garbage.
"""

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


def extract_pdf_text(path: str | Path) -> list[str]:
    """Extract one text string per page from a PDF (pymupdf, no OCR)."""
    import fitz  # pymupdf — imported lazily so the gate stays import-light

    with fitz.open(str(path)) as doc:
        return [page.get_text("text") for page in doc]


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


def _word_char_ratio(text: str) -> float:
    """Fraction of non-whitespace chars that are letters or digits (any script)."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    word_chars = sum(1 for c in non_ws if unicodedata.category(c)[0] in ("L", "N"))
    return word_chars / len(non_ws)
