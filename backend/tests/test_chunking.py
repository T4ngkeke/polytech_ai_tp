"""
test_chunking.py — [v7.1] structure-aware chunking.

Invariants:
  * The table of contents is detected and dropped (else it matches every query).
  * TD/TP exercises are split on boundaries and never cut in half.
  * Code blocks stay intact.
  * Chunks carry their source page and section heading.
Pure logic over page strings — no model, no DB.
"""

import pytest

from backend.app.models import DocType
from backend.worker.chunking import (
    chunk_pages,
    detect_numbering_anomaly,
)


def test_table_of_contents_page_is_dropped():
    toc = (
        "Table des matieres\n"
        "1 Introduction ................... 3\n"
        "1.1 Codage ....................... 5\n"
        "2 Numeration ..................... 8\n"
    )
    body = "Le codage binaire represente les nombres en base deux. " * 5
    chunks = chunk_pages([toc, body], DocType.CM)

    joined = " ".join(c.content for c in chunks)
    assert "Table des matieres" not in joined
    assert "Introduction" not in joined
    assert "codage binaire" in joined


def test_td_splits_on_exercise_boundaries_without_cutting():
    page = (
        "Exercice 1\n"
        "Convertir 42 en binaire.\n"
        "Donner le resultat en hexadecimal.\n"
        "\n"
        "Exercice 2\n"
        "Additionner 1010 et 0110 en binaire.\n"
    )
    chunks = chunk_pages([page], DocType.TD)

    assert len(chunks) == 2
    # Each exercise is whole and not merged with its neighbour.
    assert "Convertir 42 en binaire." in chunks[0].content
    assert "hexadecimal" in chunks[0].content
    assert "Additionner" not in chunks[0].content
    assert "Additionner 1010" in chunks[1].content
    assert "Convertir" not in chunks[1].content
    # The exercise label becomes the section.
    assert chunks[0].section == "Exercice 1"
    assert chunks[1].section == "Exercice 2"


def test_exercise_spanning_a_page_break_is_not_cut():
    pages = [
        "Exercice 1\nDebut de l'enonce sur la page un.",   # page 1 — exercise starts
        "Suite de l'enonce sur la page deux.\n",            # page 2 — continuation
    ]
    chunks = chunk_pages(pages, DocType.TD)

    assert len(chunks) == 1
    assert "Debut de l'enonce" in chunks[0].content
    assert "Suite de l'enonce" in chunks[0].content
    assert chunks[0].page_no == 1


# --- [v7.3] expanded exercise boundaries ------------------------------------
# Real French TDs number exercises in more styles than "Exercice <digit>":
# Roman numerals, "Question n", and bare numbered headings all mark boundaries.

def test_roman_numeral_exercise_boundaries():
    page = (
        "Exercice I\n"
        "Convertir 42 en binaire.\n"
        "\n"
        "Exercice II\n"
        "Additionner deux nombres.\n"
    )
    chunks = chunk_pages([page], DocType.TD)
    assert len(chunks) == 2
    assert chunks[0].section == "Exercice I"
    assert chunks[1].section == "Exercice II"


def test_question_keyword_boundaries():
    page = (
        "Question 1\n"
        "Definir la recursivite.\n"
        "\n"
        "Q2.\n"
        "Donner un exemple.\n"
    )
    chunks = chunk_pages([page], DocType.TD)
    assert len(chunks) == 2
    assert "recursivite" in chunks[0].content
    assert "exemple" in chunks[1].content
    # Real boundary segmentation, not the paragraph fallback: labels captured.
    assert chunks[0].section == "Question 1"
    assert chunks[1].section is not None and chunks[1].section.startswith("Q2")


def test_bare_numbered_heading_boundaries():
    # French TDs often number exercises as bare headings: "3. Écrire une fonction…"
    page = (
        "1. Ecrire une fonction somme.\n"
        "Elle prend deux entiers.\n"
        "\n"
        "2. Ecrire une fonction produit.\n"
        "Elle retourne le produit.\n"
    )
    chunks = chunk_pages([page], DocType.TD)
    assert len(chunks) == 2
    assert "somme" in chunks[0].content
    assert "produit" in chunks[1].content
    assert "produit" not in chunks[0].content
    # Labels captured from the headings — not the paragraph fallback.
    assert chunks[0].section is not None and chunks[0].section.startswith("1")
    assert chunks[1].section is not None and chunks[1].section.startswith("2")


def test_bare_numbers_inside_prose_are_not_boundaries():
    # A digit at line start that is list content inside ONE exercise must not
    # split it — only heading-shaped lines count.
    page = (
        "Exercice 1\n"
        "Calculer les valeurs suivantes :\n"
        "1 + 1\n"
        "2 + 3\n"
    )
    chunks = chunk_pages([page], DocType.TD)
    assert len(chunks) == 1


# --- [v7.3] CM size control + code-block protection ---------------------------
# Blank-line splitting alone produces noise chunks (3-word headers) and giant
# chunks (dense pages). Tiny neighbours merge; oversized paragraphs split at
# sentence boundaries; indented code blocks (which contain blank lines) never split.

def test_tiny_cm_paragraphs_are_merged():
    page = "Titre court\n\nAutre ligne breve\n\nEncore une petite ligne"
    chunks = chunk_pages([page], DocType.CM, min_chars=100, max_chars=1000)
    assert len(chunks) == 1
    assert "Titre court" in chunks[0].content
    assert "Encore une petite ligne" in chunks[0].content


def test_oversized_cm_paragraph_is_sentence_split():
    sentence = "Le codage binaire represente les nombres en base deux. "
    page = sentence * 20  # one huge paragraph, no blank lines
    chunks = chunk_pages([page], DocType.CM, min_chars=50, max_chars=300)
    assert len(chunks) > 1
    # Splits land on sentence boundaries — every piece ends with a period.
    assert all(c.content.rstrip().endswith(".") for c in chunks)
    assert all(len(c.content) <= 360 for c in chunks)  # max + one-sentence slack


def test_code_block_with_blank_lines_stays_intact():
    page = (
        "Voici un exemple de fonction :\n"
        "\n"
        "def somme(a, b):\n"
        "    resultat = a + b\n"
        "\n"
        "    return resultat\n"
    )
    chunks = chunk_pages([page], DocType.CM, min_chars=10, max_chars=1000)
    code_chunk = next(c for c in chunks if "def somme" in c.content)
    # The blank line inside the indented block must not split it.
    assert "return resultat" in code_chunk.content


def test_repeated_paragraph_across_pages_deduped_at_paragraph_level():
    notice = ("Rappel important: rendre le compte-rendu avant vendredi soir "
              "sur la plateforme de depot.")
    pages = [
        f"{notice}\n\nLe codage binaire en base deux est fondamental.",
        f"{notice}\n\nLa numeration hexadecimale utilise seize symboles.",
    ]
    chunks = chunk_pages(pages, DocType.CM, min_chars=10, max_chars=1000)
    notice_chunks = [c for c in chunks if "Rappel important" in c.content]
    assert len(notice_chunks) == 1


# --- [v7.3] numbering-anomaly detection --------------------------------------
# Bare-number boundaries can mistake sub-questions for exercises: a parent
# "Exercice 1" with sub-items 1/2/3 segments as numbers [1, 1, 2, 3]. Duplicate
# normalized numbers signal that over-split; the caller then asks an LLM to
# re-judge the true exercise boundaries. Gaps are report-only (no LLM).

def test_duplicate_numbers_flag_an_anomaly():
    labels = ["Exercice 1", "1.", "2.", "3."]   # → [1, 1, 2, 3]
    assert detect_numbering_anomaly(labels) == "duplicate_numbers"


def test_clean_sequence_is_no_anomaly():
    assert detect_numbering_anomaly(["Exercice 1", "Exercice 2", "Exercice 3"]) is None


def test_roman_and_arabic_mix_normalizes_before_checking():
    # "Exercice I" and "1." both normalize to 1 → duplicate.
    assert detect_numbering_anomaly(["Exercice I", "1."]) == "duplicate_numbers"


def test_gap_in_numbering_is_reported_as_gap():
    assert detect_numbering_anomaly(["Exercice 1", "Exercice 2", "Exercice 4"]) == "number_gap"


def test_unnumbered_labels_are_not_judged():
    # Paragraph-fallback chunks carry no labels — nothing to judge.
    assert detect_numbering_anomaly([None, None]) is None


def test_code_block_in_tp_exercise_stays_intact():
    page = (
        "Exercice 1\n"
        "Completer la fonction suivante:\n"
        "def somme(a, b):\n"
        "    resultat = a + b\n"
        "    return resultat\n"
    )
    chunks = chunk_pages([page], DocType.TP)

    assert len(chunks) == 1
    body = chunks[0].content
    assert "def somme(a, b):" in body
    assert "    resultat = a + b" in body
    assert "    return resultat" in body
