"""
test_parsing.py — [v7.1] PDF parsing + character-yield input gate.

The gate is the "never silently ingest garbage" guard: a PDF whose extracted text
is too sparse (scanned / image-only) or too garbled is flagged for the teacher to
re-export, not ingested. Gate logic is pure (operates on page strings) so it is
unit-tested without pymupdf; the pymupdf wrapper is exercised against a real sample.
"""

from pathlib import Path

import pytest

from backend.worker.parsing import character_yield_gate, extract_pdf_text

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


@pytest.mark.skipif(not _CM_BOOK.exists(), reason="sample CM_book PDF not present")
def test_extract_real_cm_book_pdf_yields_clean_text():
    pages = extract_pdf_text(_CM_BOOK)
    # One string per page, with real selectable text.
    assert len(pages) > 1
    assert any(len(p.strip()) > 200 for p in pages)
    # The golden fixture must sail through the gate.
    assert character_yield_gate(pages).ok is True
