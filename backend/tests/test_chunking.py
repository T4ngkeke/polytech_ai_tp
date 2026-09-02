"""
test_chunking.py — course-material chunking.

Invariants:
  * The table of contents is detected and dropped (else it matches every query).
  * Code blocks stay intact.
  * Tiny neighbouring paragraphs merge; oversized ones split at sentences.
  * A paragraph repeated across pages (a recurring notice) is stored once.

Exercise-boundary segmentation and its numbering-anomaly detection are gone with
the Exercises table, and with them the `doc_type` argument — there is one kind of
document now, so `chunk_pages` no longer branches.

Pure logic over page strings — no model, no DB.
"""

from backend.worker.chunking import chunk_pages


def test_table_of_contents_page_is_dropped():
    toc = (
        "Table des matieres\n"
        "1 Introduction ................... 3\n"
        "1.1 Codage ....................... 5\n"
        "2 Numeration ..................... 8\n"
    )
    body = "Le codage binaire represente les nombres en base deux. " * 5
    chunks = chunk_pages([toc, body])

    joined = " ".join(c.content for c in chunks)
    assert "Table des matieres" not in joined
    assert "Introduction" not in joined
    assert "codage binaire" in joined


# --- size control + code-block protection -----------------------------------
# Blank-line splitting alone produces noise chunks (3-word headers) and giant
# chunks (dense pages). Tiny neighbours merge; oversized paragraphs split at
# sentence boundaries; indented code blocks (which contain blank lines) never split.

def test_tiny_paragraphs_are_merged():
    page = "Titre court\n\nAutre ligne breve\n\nEncore une petite ligne"
    chunks = chunk_pages([page], min_chars=100, max_chars=1000)
    assert len(chunks) == 1
    assert "Titre court" in chunks[0].content
    assert "Encore une petite ligne" in chunks[0].content


def test_oversized_paragraph_is_sentence_split():
    sentence = "Le codage binaire represente les nombres en base deux. "
    page = sentence * 20  # one huge paragraph, no blank lines
    chunks = chunk_pages([page], min_chars=50, max_chars=300)
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
    chunks = chunk_pages([page], min_chars=10, max_chars=1000)
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
    chunks = chunk_pages(pages, min_chars=10, max_chars=1000)
    notice_chunks = [c for c in chunks if "Rappel important" in c.content]
    assert len(notice_chunks) == 1


def test_chunks_carry_their_source_page():
    pages = [
        "Le codage binaire represente les nombres en base deux et rien d'autre.",
        "La numeration hexadecimale utilise seize symboles distincts au total.",
    ]
    chunks = chunk_pages(pages, min_chars=10, max_chars=1000)
    assert [c.page_no for c in chunks] == [1, 2]


# --- what was removed -------------------------------------------------------

def test_exercise_segmentation_helpers_are_gone():
    """Segmentation existed to build Exercises rows. With no rows to build, the
    boundary regex, the LLM line classifier and the numbering-anomaly detector
    all went with it."""
    import backend.worker.chunking as chunking

    for gone in ("detect_numbering_anomaly", "split_into_exercises",
                 "reconcile_boundaries", "classify_document",
                 "validate_page_classification", "segment_exercises",
                 "number_lines", "build_rolling_context"):
        assert not hasattr(chunking, gone), f"chunking still exports {gone}"
