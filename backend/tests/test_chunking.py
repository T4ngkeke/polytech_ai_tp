"""
test_chunking.py — [v7.1] structure-aware chunking.

Invariants:
  * The table of contents is detected and dropped (else it matches every query).
  * TD/TP exercises are split on boundaries and never cut in half.
  * Code blocks stay intact.
  * Chunks carry their source page and section heading.
Pure logic over page strings — no model, no DB.
"""

from backend.app.models import DocType
from backend.worker.chunking import chunk_pages


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
