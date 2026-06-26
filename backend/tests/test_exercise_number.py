"""
test_exercise_number.py — [v7.2] harness for the shared exercise-number
normalization.

The printed exercise label is unstable (Arabic / Roman / "3.1" / FR / ZH), so
both ingest-side extraction and query-side matching run the SAME deterministic
`normalize_exercise_number()` to map a label to one canonical integer. This
fixture set is the regression harness the user asked for.
"""

import pytest

from backend.app.agent.exercise_number import normalize_exercise_number


@pytest.mark.parametrize("raw,expected", [
    # Arabic, plain and labelled (EN/FR)
    ("2", 2),
    ("Exercise 2", 2),
    ("exercice 3", 3),
    ("Exercice  4", 4),
    ("Problème 5", 5),
    ("Problem 6", 6),
    ("Question 7", 7),
    ("Exo 8", 8),
    # Punctuation styles
    ("1.", 1),
    ("1)", 1),
    ("(1)", 1),
    ("#9", 9),
    ("Exercise 10:", 10),
    # Dotted sub-number → main number
    ("3.1", 3),
    ("3.1.2", 3),
    ("Exercise 12.4", 12),
    # Roman numerals (with/without keyword)
    ("II", 2),
    ("iv", 4),
    ("Exercise III", 3),
    ("Exercice IX", 9),
    ("VII", 7),
    # Chinese
    ("第一题", 1),
    ("练习三", 3),
    ("习题十", 10),
    ("第12题", 12),
    # Surrounding whitespace
    ("  Exercise 2  ", 2),
])
def test_normalizes_to_canonical_int(raw, expected):
    assert normalize_exercise_number(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    None,
    "intro",
    "summary",
    "no number here",
])
def test_returns_none_when_no_number(raw):
    assert normalize_exercise_number(raw) is None


def test_ingest_and_query_labels_match_via_normalization():
    # A stored label "Exercice II" and a student's "exercise 2" collapse to 2.
    assert normalize_exercise_number("Exercice II") == normalize_exercise_number("exercise 2")
