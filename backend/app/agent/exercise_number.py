"""
exercise_number.py — [v7.2] shared exercise-number normalization.

The printed exercise label is unstable across documents (Arabic, Roman, "3.1",
FR/EN keywords), which makes regex-only matching brittle. Both the
ingest-side extraction and the query-side router run this ONE deterministic
function so a label and a student's reference collapse to the same canonical
integer.

Pure, dependency-free, fully covered by ``test_exercise_number.py``.
"""

from __future__ import annotations

import re

_ARABIC_RE = re.compile(r"\d+")
# [v7.3] Document-level prefix ("TD 2", "TP1", "CM 3"): its digits are the
# document number, never the exercise number — strip before reading digits.
_DOC_PREFIX_RE = re.compile(r"(?i)\b(?:td|tp|cm)\s*\d+")
# Restrict to I/V/X only: exercise numbering never exceeds ~XXXIX, and excluding
# L/C/D/M avoids mis-reading lettered sub-parts ("Problème C") and ordinary words
# ("mix", "civil") as Roman numerals.
_ROMAN_TOKEN_RE = re.compile(r"(?i)\b([ivx]+)\b")
_ROMAN_VALID_RE = re.compile(r"(?i)^(X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


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


def normalize_exercise_number(raw: str | None) -> int | None:
    """Map an exercise label to its canonical integer, or ``None`` if absent.

    Precedence: Arabic digits (``"3.1"`` → ``3``) → Roman numerals. Keyword
    prefixes (exercise/exercice/problème/…) are ignored. Course documents are
    FR/EN only — non-Latin numerals are not parsed.
    """
    if not raw:
        return None
    text = _DOC_PREFIX_RE.sub("", raw).strip()

    # 1. Arabic integer — the first run of digits (so "3.1" → 3, "Exercise 2" → 2).
    arabic = _ARABIC_RE.search(text)
    if arabic:
        return int(arabic.group())

    # 2. Roman numeral — a standalone, *valid* roman token.
    for token in _ROMAN_TOKEN_RE.findall(text):
        if _ROMAN_VALID_RE.match(token):
            return _roman_to_int(token)

    return None


# [v8.0 §3.5] A leading structural keyword on a heading line (before the ordinal).
_HEADING_KEYWORD_RE = re.compile(
    r"(?i)^\s*(?:exercice|exercise|exo|probl[eè]me|problem|question|partie|section|q)"
    r"\b[\s.:#)°\-–—]*"
)
_ARABIC_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)*")
_ROMAN_TOKEN_RE_START = re.compile(r"(?i)^[ivx]+")
_LETTER_TOKEN_RE = re.compile(r"(?i)^[a-z](?![a-z])")


def normalize_heading_number(heading: str | None) -> int | None:
    """Canonical number for a full heading LINE that became an exercise boundary.

    Unlike ``normalize_exercise_number`` (which scans the whole string for the
    first digit), this reads only the leading ordinal TOKEN, so digits inside the
    title never masquerade as the number: ``"III - Base 2 et base 16"`` → 3, not 2.

    A leading letter ordinal (``"Partie A"``) is not mapped → ``None`` (we don't
    support letter numbering). When there is no leading ordinal token at all, it
    falls back to whole-string normalization (arabic ordinals already come before
    any title digit, so that stays correct).
    """
    if not heading:
        return None
    text = _DOC_PREFIX_RE.sub("", heading).strip()
    core = _HEADING_KEYWORD_RE.sub("", text).strip()

    arabic = _ARABIC_TOKEN_RE.match(core)
    if arabic:
        return normalize_exercise_number(arabic.group())

    roman = _ROMAN_TOKEN_RE_START.match(core)
    if roman and _ROMAN_VALID_RE.match(roman.group()):
        return _roman_to_int(roman.group())

    if _LETTER_TOKEN_RE.match(core):
        return None  # letter ordinal — deliberately unsupported

    # No leading ordinal token → current whole-string behaviour.
    return normalize_exercise_number(text)


def is_document_label(text: str | None) -> bool:
    """True if ``text`` is a bare document label — ``"TD 1 - Codage"``, ``"TP 2"``,
    ``"CM 3"`` — i.e. it starts with a ``TD/TP/CM n`` prefix and carries no
    exercise ordinal of its own. Such a line is the document TITLE, never an
    exercise boundary; the LLM line-classifier occasionally mislabels it as a
    heading, so the segmenter drops it deterministically. ``"TD 1 Exercice 2"``
    is NOT a document label (it has its own ordinal → 2)."""
    if not text:
        return False
    if not _DOC_PREFIX_RE.match(text.strip()):
        return False
    return normalize_heading_number(text) is None
