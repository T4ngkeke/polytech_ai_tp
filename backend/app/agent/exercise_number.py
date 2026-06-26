"""
exercise_number.py — [v7.2] shared exercise-number normalization.

The printed exercise label is unstable across documents (Arabic, Roman, "3.1",
FR/EN keywords, Chinese), which makes regex-only matching brittle. Both the
ingest-side extraction and the query-side router run this ONE deterministic
function so a label and a student's reference collapse to the same canonical
integer.

Pure, dependency-free, fully covered by ``test_exercise_number.py``.
"""

from __future__ import annotations

import re

_ARABIC_RE = re.compile(r"\d+")
# Restrict to I/V/X only: exercise numbering never exceeds ~XXXIX, and excluding
# L/C/D/M avoids mis-reading lettered sub-parts ("Problème C") and ordinary words
# ("mix", "civil") as Roman numerals.
_ROMAN_TOKEN_RE = re.compile(r"(?i)\b([ivx]+)\b")
_ROMAN_VALID_RE = re.compile(r"(?i)^(X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

_CJK_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def _roman_to_int(token: str) -> int:
    total = 0
    prev = 0
    for ch in reversed(token.lower()):
        value = _ROMAN_VALUES[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


def _cjk_to_int(text: str) -> int | None:
    """Parse a small Chinese numeral (covers 一..九, 十, and 十X / X十 / X十Y)."""
    chars = [c for c in text if c in _CJK_DIGITS or c == "十"]
    if not chars:
        return None
    if chars == ["十"]:
        return 10
    if "十" not in chars:
        # Single digit only (multi-digit CJK without 十 is not standard).
        return _CJK_DIGITS.get(chars[0]) if len(chars) == 1 else None
    idx = chars.index("十")
    tens = _CJK_DIGITS.get(chars[idx - 1], 1) if idx > 0 else 1
    units = _CJK_DIGITS.get(chars[idx + 1], 0) if idx + 1 < len(chars) else 0
    return tens * 10 + units


def normalize_exercise_number(raw: str | None) -> int | None:
    """Map an exercise label to its canonical integer, or ``None`` if absent.

    Precedence: Arabic digits (``"3.1"`` → ``3``) → Roman numerals → Chinese
    numerals. Keyword prefixes (exercise/exercice/problème/…) are ignored.
    """
    if not raw:
        return None
    text = raw.strip()

    # 1. Arabic integer — the first run of digits (so "3.1" → 3, "Exercise 2" → 2).
    arabic = _ARABIC_RE.search(text)
    if arabic:
        return int(arabic.group())

    # 2. Roman numeral — a standalone, *valid* roman token.
    for token in _ROMAN_TOKEN_RE.findall(text):
        if _ROMAN_VALID_RE.match(token):
            return _roman_to_int(token)

    # 3. Chinese numeral.
    return _cjk_to_int(text)
