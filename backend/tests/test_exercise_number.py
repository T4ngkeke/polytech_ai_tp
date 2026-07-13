"""
test_exercise_number.py — [v7.2] harness for the shared exercise-number
normalization.

The printed exercise label is unstable (Arabic / Roman / "3.1" / FR / ZH), so
both ingest-side extraction and query-side matching run the SAME deterministic
`normalize_exercise_number()` to map a label to one canonical integer. This
fixture set is the regression harness the user asked for.
"""

import pytest

from backend.app.agent.exercise_number import (
    normalize_exercise_number,
    normalize_heading_number,
)


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
    # [v7.2 fix] do NOT mis-read lettered labels / ordinary words as Roman numerals.
    "c",            # was → 100
    "Problème C",   # was → 100 (lettered sub-part, not exercise 100)
    "Partie M",     # was → 1000
    "m",            # was → 1000
    "mix",          # was → 1009
    "civil",        # contains c/i/v/l
])
def test_returns_none_when_no_number(raw):
    assert normalize_exercise_number(raw) is None


def test_ingest_and_query_labels_match_via_normalization():
    # A stored label "Exercice II" and a student's "exercise 2" collapse to 2.
    assert normalize_exercise_number("Exercice II") == normalize_exercise_number("exercise 2")


# [v7.3] Document-level prefixes (TD n / TP n / CM n) must not be read as the
# exercise number: "TD 2 – Exercice 3" is exercise 3 of document TD 2, not
# exercise 2. The first-digit-run rule alone gets this wrong.
@pytest.mark.parametrize("raw,expected", [
    ("TD 2 – Exercice 3", 3),
    ("TP1 Exercice 2", 2),
    ("td3 - exercise 5", 5),
    ("CM 1 Question 4", 4),
    ("TD 2 TP 3 Exercice 7", 7),      # multiple prefixes all stripped
    ("l'exercice 3 du TD 2", 3),      # query-side phrasing: exercise digits come first
])
def test_strips_doc_prefix_before_number(raw, expected):
    assert normalize_exercise_number(raw) == expected


@pytest.mark.parametrize("raw", [
    "TD 2",       # a bare document label is not an exercise number
    "TP 1",
])
def test_bare_doc_label_is_not_an_exercise(raw):
    assert normalize_exercise_number(raw) is None


# [v8.0 §3.5] Heading-number: when a full heading LINE becomes an exercise
# boundary, only its leading ordinal TOKEN is normalized — so digits inside the
# title never masquerade as the number (the section-promotion silent-poison bug).
@pytest.mark.parametrize("heading,expected", [
    # Roman section headings — the ordinal, not a digit in the title.
    ("I - Codage et décodage", 1),
    ("II - Numération", 2),
    ("II - Base 2 et base 16", 2),      # roman II, NOT the "2" in the title
    ("III - Base 2 et base 16", 3),     # ⭐ regression anchor: whole-line normalize returns 2 here
    # Keyword + arabic — the token extractor must not break existing cases.
    ("Exercice 3 : conversion en base 2", 3),
    ("Exercise 10: two's complement", 10),
    # Keyword + roman + title digit — keyword strip then read the roman token.
    ("Exercice III : base 2", 3),
    # Bare arabic / dotted.
    ("3. Écrire une fonction", 3),
    ("3.1 sous-question", 3),
])
def test_heading_number_reads_the_ordinal_token(heading, expected):
    assert normalize_heading_number(heading) == expected


@pytest.mark.parametrize("heading", [
    "Partie A",          # letter ordinal → not normalizable (we don't map letters)
    "Partie B",
    "Tri fusion",        # topic-only heading, no ordinal at all
])
def test_heading_number_none_for_letter_or_topic(heading):
    assert normalize_heading_number(heading) is None
