"""
test_parsing.py — [v7.1] PDF parsing + character-yield input gate.

The gate is the "never silently ingest garbage" guard: a PDF whose extracted text
is too sparse (scanned / image-only) or too garbled is flagged for the teacher to
re-export, not ingested. Gate logic is pure (operates on page strings) so it is
unit-tested without pymupdf; the pymupdf wrapper is exercised against a real sample.
"""

from pathlib import Path

import pytest

from backend.worker.parsing import (
    character_yield_gate,
    extract_pdf_text,
    strip_repeated_lines,
)

# The clean digital-born "golden smoke fixture" from the real course corpus.
_CM_BOOK = Path(__file__).resolve().parents[2] / "docs" / "CM_book" / "INFO501.pdf"


def test_gate_rejects_near_empty_pages():
    # A scanned / image-only PDF extracts almost no selectable text.
    result = character_yield_gate(["", "  ", "\n"])
    assert result.ok is False
    assert "char" in result.reason.lower()


def test_gate_accepts_clean_text_pages():
    pages = [
        "Chapitre 1. Codage et numeration. " * 20,
        "Exercice 1: convertir 42 en binaire. " * 20,
    ]
    result = character_yield_gate(pages)
    assert result.ok is True
    assert result.reason is None


def test_gate_rejects_garbled_high_nonword_pages():
    # Enough characters, but mostly non-word symbols (a broken/garbled extraction).
    garbled = "�@#~^|` □•\t " * 60
    result = character_yield_gate([garbled, garbled])
    assert result.ok is False
    assert "word" in result.reason.lower() or "garbl" in result.reason.lower()


# --- [v7.3] header/footer stripping -----------------------------------------
# Repeated headers ("TD3 – Informatique" on every page) pollute chunks and
# number extraction; strip lines that recur on >= min_repeat pages.

def test_strips_line_repeated_on_enough_pages():
    pages = [
        "TD3 – Informatique – Polytech\nExercice 1\nÉcrire une fonction.",
        "TD3 – Informatique – Polytech\nExercice 2\nTrier une liste.",
        "TD3 – Informatique – Polytech\nExercice 3\nInverser une chaîne.",
    ]
    cleaned = strip_repeated_lines(pages)
    assert all("TD3 – Informatique" not in p for p in cleaned)
    # Real content survives, page count unchanged.
    assert len(cleaned) == 3
    assert "Écrire une fonction." in cleaned[0]
    assert "Exercice 2" in cleaned[1]


def test_page_number_variants_count_as_the_same_footer():
    # "page 1" / "page 2" / "page 3" differ only by digits — one footer.
    pages = [
        "Contenu A\npage 1",
        "Contenu B\npage 2",
        "Contenu C\npage 3",
    ]
    cleaned = strip_repeated_lines(pages)
    assert all("page" not in p for p in cleaned)
    assert cleaned[0].strip() == "Contenu A"


def test_keeps_lines_below_min_repeat():
    # Repeated on only 2 of 3 pages with default min_repeat=3 → kept.
    pages = [
        "Rappel: utiliser la récursivité\nExercice 1",
        "Rappel: utiliser la récursivité\nExercice 2",
        "Exercice 3",
    ]
    cleaned = strip_repeated_lines(pages)
    assert "Rappel" in cleaned[0]
    assert "Rappel" in cleaned[1]


def test_blank_lines_are_structure_not_footers():
    # Blank lines separate paragraphs; they must never be stripped as "repeated".
    pages = [
        "Para A1\n\nPara A2",
        "Para B1\n\nPara B2",
        "Para C1\n\nPara C2",
    ]
    cleaned = strip_repeated_lines(pages)
    assert "\n\n" in cleaned[0]


def test_min_repeat_is_tunable_for_short_docs():
    # A 2-page TD: caller lowers the threshold to strip its header.
    pages = ["En-tête TD1\nExercice 1", "En-tête TD1\nExercice 2"]
    cleaned = strip_repeated_lines(pages, min_repeat=2)
    assert all("En-tête" not in p for p in cleaned)


@pytest.mark.skipif(not _CM_BOOK.exists(), reason="sample CM_book PDF not present")
def test_extract_real_cm_book_pdf_yields_clean_text():
    pages = extract_pdf_text(_CM_BOOK)
    # One string per page, with real selectable text.
    assert len(pages) > 1
    assert any(len(p.strip()) > 200 for p in pages)
    # The golden fixture must sail through the gate.
    assert character_yield_gate(pages).ok is True
