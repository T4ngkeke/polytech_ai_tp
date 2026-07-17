"""
test_vlm.py — [v8.1 item 8 stage 2] VLM garbled-formula repair.

Detection is deterministic (spike-validated rules + the spike's two
false-positive fixes: code lines and dot-leader junk are never flagged).
Repair is transcribe-only with a hard safety check: a transcription that
introduces a NEW exercise boundary is refused (the VLM must never invent
structure). Every region lands in an audit trail (before/after) and no
failure ever breaks ingestion. render/transcribe are injected fakes here.
"""

from pathlib import Path

import pytest

from backend.worker.vlm import find_garbled_regions, repair_pages

_TD1 = (Path(__file__).resolve().parents[2] / "docs" / "TD"
        / "TD 1 - Codage et numération — Tds.pdf")


GARBLED = "N = (e −1)215 + (d −1)211 + (c −1)27 + (b −1)22 + (a −1),"
MATHY = "𝑁10 = −1𝑆.(1, 𝑀2.2(𝐸2)10−127)10."


# --- detection ---------------------------------------------------------------

def test_detects_mangled_formula_lines():
    pages = [f"Exercice 1\nSoit un nombre.\n{GARBLED}\nConvertir en binaire."]
    regions = find_garbled_regions(pages)
    assert len(regions) == 1
    r = regions[0]
    assert r.page_index == 0
    assert r.lines == [GARBLED]


def test_detects_unicode_math_lines():
    pages = [f"Rappel :\n{MATHY}\nfin."]
    assert len(find_garbled_regions(pages)) == 1


def test_adjacent_garbled_lines_merge_into_one_region():
    pages = [f"intro\n{GARBLED}\n{MATHY}\nfin."]
    regions = find_garbled_regions(pages)
    assert len(regions) == 1
    assert regions[0].lines == [GARBLED, MATHY]


def test_code_lines_are_never_flagged():
    """Spike finding: low word-ratio false-positives on code. Indented lines
    (code blocks) are exempt — code is fine as text."""
    pages = ["Exercice 1\nRecopier :\n    A a1 = new A(...);\n    x = b1; → {}\nfin."]
    assert find_garbled_regions(pages) == []


def test_dot_leaders_are_never_flagged():
    pages = ["Sommaire\n... ...\n........\nContenu normal ici."]
    assert find_garbled_regions(pages) == []


def test_clean_pages_yield_no_regions():
    pages = ["Exercice 1\nÉcrire une fonction qui trie une liste.\n"]
    assert find_garbled_regions(pages) == []


# --- repair orchestration ----------------------------------------------------

def _fake_render(page_index, lines):
    return b"\x89PNGfake"


@pytest.mark.asyncio
async def test_repair_replaces_lines_and_audits():
    pages = [f"Exercice 1\nSoit :\n{GARBLED}\nConvertir."]

    async def transcribe(png):
        return "N = (e-1)2^{15} + (d-1)2^{11} + (c-1)2^7 + (b-1)2^2 + (a-1)"

    repaired, entries = await repair_pages(
        pages, render_fn=_fake_render, transcribe_fn=transcribe)
    assert "2^{15}" in repaired[0]
    assert GARBLED not in repaired[0]
    assert "Exercice 1" in repaired[0]          # untouched lines survive
    assert "Convertir." in repaired[0]
    assert len(entries) == 1
    e = entries[0]
    assert e["status"] == "replaced"
    assert e["page"] == 1
    assert GARBLED in e["before"]
    assert "2^{15}" in e["after"]


@pytest.mark.asyncio
async def test_repair_refuses_a_transcription_that_invents_a_boundary():
    """Red line: the VLM transcribes, it never invents structure. Output that
    introduces a NEW exercise heading is refused — original text kept."""
    pages = [f"Exercice 1\n{GARBLED}\nfin."]

    async def transcribe(png):
        return "Exercice 9\nN = 2^{15}"

    repaired, entries = await repair_pages(
        pages, render_fn=_fake_render, transcribe_fn=transcribe)
    assert repaired[0] == pages[0]              # untouched
    assert entries[0]["status"] == "refused_new_boundary"


@pytest.mark.asyncio
async def test_repair_skips_unreadable_transcriptions():
    pages = [f"intro\n{GARBLED}\nfin."]

    async def transcribe(png):
        return "[illisible]"

    repaired, entries = await repair_pages(
        pages, render_fn=_fake_render, transcribe_fn=transcribe)
    assert repaired[0] == pages[0]
    assert entries[0]["status"] == "unreadable"


@pytest.mark.asyncio
async def test_repair_failure_never_raises_and_other_regions_proceed():
    pages = [f"a\n{GARBLED}\nb.", f"c\n{MATHY}\nd."]
    calls = {"n": 0}

    async def transcribe(png):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("vision endpoint down")
        return "propre : $x$"

    repaired, entries = await repair_pages(
        pages, render_fn=_fake_render, transcribe_fn=transcribe)
    assert repaired[0] == pages[0]              # failed region kept as-is
    assert "$x$" in repaired[1]                 # later region still repaired
    statuses = {e["status"] for e in entries}
    assert statuses == {"failed", "replaced"}


# --- rendering (real-PDF smoke; I/O glue) ------------------------------------

@pytest.mark.skipif(not _TD1.exists(), reason="sample TD1 PDF not present")
def test_render_region_crops_real_pdf_to_png():
    """The known garbled formula on TD1 p.3 must render to a PNG crop smaller
    than the full page (bbox matching worked)."""
    from backend.worker.vlm import render_region

    line = "N = (e −1)215 + (d −1)211 + (c −1)27 + (b −1)22 + (a −1),"
    png = render_region(str(_TD1), 2, [line])
    assert png[:4] == b"\x89PNG"
    full = render_region(str(_TD1), 2, ["no such line on this page"])
    assert full[:4] == b"\x89PNG"
    assert len(png) < len(full)      # a crop, not the fallback full page
