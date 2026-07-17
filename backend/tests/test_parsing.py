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
    html_to_markdown,
    split_markdown_pages,
    strip_repeated_lines,
)

# The clean digital-born "golden smoke fixture" from the real course corpus.
_CM_BOOK = Path(__file__).resolve().parents[2] / "docs" / "CM_book" / "INFO501.pdf"
# A real TD whose page 3 has a two-column / formula layout that plain pymupdf
# extraction reorders wrongly (variables pile up at page end, leaving voids).
_TD1 = (Path(__file__).resolve().parents[2] / "docs" / "TD"
        / "TD 1 - Codage et numération — Tds.pdf")


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


@pytest.mark.skipif(not _TD1.exists(), reason="sample TD1 PDF not present")
def test_extract_uses_reading_order_so_formula_text_is_intact():
    """[v8.0] pymupdf must extract in reading order (sort=True); otherwise the
    two-column formula layout on TD1 page 3 reorders and the running prose gets a
    void where a variable was pulled to the page end."""
    pages = extract_pdf_text(_TD1)
    page3 = pages[2]
    # This exact prose only reassembles when text is read in visual order.
    assert "correspondant au casier a de la" in page3


@pytest.mark.skipif(not _TD1.exists(), reason="sample TD1 PDF not present")
def test_strip_repeated_lines_still_removes_header_after_sort():
    """[v8.0] Regression guard: sort=True keeps the running header on every page,
    so strip_repeated_lines must still remove it (real TD1 header repeats on all
    3 pages)."""
    pages = extract_pdf_text(_TD1)
    header = "TD 1 - Codage et numération — Tds"
    assert any(header in p for p in pages)                 # present before
    stripped = strip_repeated_lines(pages)
    assert all(header not in p for p in stripped)          # gone after


# ---------------------------------------------------------------------------
# [v8.1] Markdown "pages": split on the shallowest heading level present, so a
# doc using only "##" still splits; heading-like lines inside ``` fences (e.g.
# Python comments) never split; no headings → one page.
# ---------------------------------------------------------------------------

def test_split_markdown_pages_on_top_level_headings():
    text = (
        "# Chapitre 1\ncontenu un\n\n"
        "## sous-section\nplus\n\n"
        "# Chapitre 2\ncontenu deux\n"
    )
    pages = split_markdown_pages(text)
    assert len(pages) == 2
    assert pages[0].startswith("# Chapitre 1")
    assert "sous-section" in pages[0]          # deeper headings don't split
    assert pages[1].startswith("# Chapitre 2")


def test_split_markdown_pages_uses_shallowest_level_present():
    text = "## A\nun\n\n## B\ndeux\n"          # no "#" at all → "##" splits
    pages = split_markdown_pages(text)
    assert len(pages) == 2
    assert pages[0].startswith("## A")
    assert pages[1].startswith("## B")


def test_split_markdown_pages_preamble_is_own_page():
    text = "intro avant tout titre\n\n# Un\ncorps\n"
    pages = split_markdown_pages(text)
    assert len(pages) == 2
    assert pages[0].startswith("intro")
    assert pages[1].startswith("# Un")


def test_split_markdown_pages_ignores_hashes_inside_code_fences():
    text = (
        "# Exercice 1\n"
        "```python\n"
        "# ceci est un commentaire, pas un titre\n"
        "x = 1\n"
        "```\n"
        "# Exercice 2\nsuite\n"
    )
    pages = split_markdown_pages(text)
    assert len(pages) == 2
    assert "commentaire" in pages[0]


def test_split_markdown_pages_no_headings_single_page():
    text = "juste du texte\nsur deux lignes\n"
    assert split_markdown_pages(text) == [text.strip()]


# ---------------------------------------------------------------------------
# [v8.1] HTML → markdown-ish text. Teachers' HTML (when they have it) is clean;
# the converter turns headings into #-lines (so split_markdown_pages pages it),
# strips chrome (script/style/nav/header/footer/aside), recovers the ORIGINAL
# LaTeX that KaTeX/MathJax embed in their rendered output (the whole reason
# HTML beats a printed PDF), and fences <pre> so code never fakes a heading.
# ---------------------------------------------------------------------------

def test_html_headings_become_md_headings():
    html = "<h1>Chapitre 1</h1><p>intro</p><h2>Section</h2><p>corps</p>"
    text = html_to_markdown(html)
    lines = [l for l in text.splitlines() if l.strip()]
    assert "# Chapitre 1" in lines
    assert "## Section" in lines
    assert any("intro" in l for l in lines)


def test_html_chrome_is_stripped():
    html = (
        "<nav>menu menu</nav><header>site banner</header>"
        "<script>var x=1;</script><style>.a{}</style>"
        "<main><p>le vrai contenu</p></main>"
        "<footer>copyright</footer><aside>pub</aside>"
    )
    text = html_to_markdown(html)
    assert "le vrai contenu" in text
    for junk in ("menu", "banner", "var x=1", "copyright", "pub", ".a{}"):
        assert junk not in text


def test_html_katex_annotation_recovers_latex():
    # KaTeX rendering: visible span soup + the original LaTeX hidden in MathML.
    html = (
        "<p>Einstein : <span class=\"katex\">"
        "<span class=\"katex-html\">E=mc<sup>2</sup></span>"
        "<math><semantics><mrow></mrow>"
        "<annotation encoding=\"application/x-tex\">E=mc^2</annotation>"
        "</semantics></math></span> voilà.</p>"
    )
    text = html_to_markdown(html)
    assert "$E=mc^2$" in text           # the original LaTeX, recovered
    assert "katex" not in text
    assert text.count("E=mc") == 1      # rendered duplicate removed


def test_html_mathjax_v2_script_recovers_latex():
    html = "<p>Soit <script type=\"math/tex\">x^2 + 1</script> un polynôme.</p>"
    text = html_to_markdown(html)
    assert "$x^2 + 1$" in text
    assert "un polynôme" in text


def test_html_pre_is_fenced_and_never_a_heading():
    html = "<h1>Exercice 1</h1><pre># un commentaire python\nx = 1</pre><h1>Exercice 2</h1><p>suite</p>"
    text = html_to_markdown(html)
    assert "```" in text
    # The fenced hash line must not page-split: 2 pages, comment stays in page 1.
    pages = split_markdown_pages(text)
    assert len(pages) == 2
    assert "commentaire" in pages[0]


def test_html_paragraphs_get_blank_lines():
    html = "<p>premier paragraphe</p><p>deuxième paragraphe</p>"
    text = html_to_markdown(html)
    assert "premier paragraphe\n\ndeuxième paragraphe" in text.replace("\n\n\n", "\n\n")
