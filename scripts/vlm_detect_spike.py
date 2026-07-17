"""
vlm_detect_spike.py — [v8.1 item 8, spike stage 1] garbled-line detection.

Before building the VLM formula-repair path, verify the DETERMINISTIC part:
can cheap per-line rules find the mangled-math lines in real course PDFs,
with few false positives? Run this over the real corpus and eyeball the hits.

Rules (a line is suspicious when any fires):
  A. low word-char ratio  — letters+digits / non-space chars < 0.55
     (whole-page gate uses 0.5; per-line we can be slightly stricter)
  B. math-symbol density  — >= 3 non-ASCII math/symbol chars and they make up
     >= 15% of non-space chars (∑ ∫ √ ⌊ × ± … and the Unicode math alphabets)
  C. replacement/mojibake — any U+FFFD or PUA char

Adjacent suspicious lines merge into one region (what we would crop and send
to the VLM in stage 2).

Usage:
  python scripts/vlm_detect_spike.py [pdf_or_dir ...]      (default: docs/)
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.worker.parsing import extract_pdf_text, strip_repeated_lines  # noqa: E402

WORD_RATIO_MIN = 0.55
MATH_COUNT_MIN = 3
MATH_RATIO_MIN = 0.15
MIN_LINE_CHARS = 4          # ignore tiny fragments ("a)", "2.")


def _non_space(line: str) -> list[str]:
    return [c for c in line if not c.isspace()]


def word_char_ratio(line: str) -> float:
    chars = _non_space(line)
    if not chars:
        return 1.0
    word = sum(1 for c in chars if unicodedata.category(c)[0] in ("L", "N"))
    return word / len(chars)


def _is_mathy(c: str) -> bool:
    if ord(c) < 128:
        return False
    cat = unicodedata.category(c)
    if cat in ("Sm", "So"):                      # math / other symbol
        return True
    # Unicode math alphanumeric block (𝑎, 𝐸, ℕ …) and letterlike symbols.
    return 0x1D400 <= ord(c) <= 0x1D7FF or 0x2100 <= ord(c) <= 0x214F


def math_density(line: str) -> tuple[int, float]:
    chars = _non_space(line)
    if not chars:
        return 0, 0.0
    mathy = sum(1 for c in chars if _is_mathy(c))
    return mathy, mathy / len(chars)


def has_mojibake(line: str) -> bool:
    return any(c == "�" or 0xE000 <= ord(c) <= 0xF8FF for c in line)


def classify_line(line: str) -> str | None:
    """Return the rule name that fires, or None for a clean line."""
    if len(line.strip()) < MIN_LINE_CHARS:
        return None
    if has_mojibake(line):
        return "mojibake"
    count, ratio = math_density(line)
    if count >= MATH_COUNT_MIN and ratio >= MATH_RATIO_MIN:
        return "math-density"
    if word_char_ratio(line) < WORD_RATIO_MIN:
        return "low-word-ratio"
    return None


def scan_pdf(path: Path) -> None:
    pages = strip_repeated_lines(extract_pdf_text(path))
    total_lines = 0
    regions = 0
    flagged = 0
    print(f"\n=== {path.name} ===")
    for page_no, page in enumerate(pages, 1):
        current: list[tuple[int, str, str]] = []   # (line_no, rule, text)
        for line_no, line in enumerate(page.splitlines(), 1):
            if line.strip():
                total_lines += 1
            rule = classify_line(line)
            if rule:
                flagged += 1
                current.append((line_no, rule, line))
            elif current:
                _report_region(page_no, current)
                regions += 1
                current = []
        if current:
            _report_region(page_no, current)
            regions += 1
    pct = 100 * flagged / total_lines if total_lines else 0
    print(f"  → {flagged}/{total_lines} lines flagged ({pct:.1f}%), "
          f"{regions} region(s)")


def _report_region(page_no: int, lines: list[tuple[int, str, str]]) -> None:
    first, last = lines[0][0], lines[-1][0]
    print(f"  p.{page_no} lines {first}-{last}:")
    for _, rule, text in lines:
        preview = " ".join(text.split())[:90]
        print(f"    [{rule:>14}] {preview}")


def main() -> None:
    args = sys.argv[1:] or ["docs"]
    pdfs: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            pdfs.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            pdfs.append(p)
    if not pdfs:
        print("No PDFs found.", file=sys.stderr)
        sys.exit(1)
    for pdf in pdfs:
        try:
            scan_pdf(pdf)
        except Exception as exc:  # a spike keeps going past one bad file
            print(f"  !! {pdf.name}: {exc}")


if __name__ == "__main__":
    main()
