"""Stricter judgements for the MinerU comparison round.

The first round accepted a check too easily: a page that had "an image" passed,
and text passed when only its non-whitespace characters matched. Those rules
cannot tell a real figure from a placeholder, or a preserved code line from a
reflowed one, so they are replaced here.

What changed:
- text is judged on characters, whitespace and symbols separately;
- a figure must be the target object's own picture: decodable, not a filler
  reused for several objects, and in the source object's aspect ratio;
- a table is judged on cell values and their row and column positions;
- question evidence names the exact sentence, table or figure it needs;
- a missing coordinate or source id is recorded as missing, never derived.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .common import sha256_json, utc_now
from .supplement import embedded_table

ASPECT_TOLERANCE = 0.20
# A difference-hash distance is recorded as supporting evidence, never as the
# verdict. The reference is a crop we cut out of our own rendered preview, so it
# carries a slight offset against the parser's own copy of the picture, and the
# hash reacts strongly to that offset. Judging on it marked a correct extraction
# as wrong, so the verdict now rests on checks that cannot drift: the aspect
# ratio of the source object, whether the same file is handed out for several
# objects, whether it opens, and whether it has any content at all.
DHASH_SUPPORTS = 16
FLAT_IMAGE_STDDEV = 3.0


def dhash(path: Path, size: int = 8) -> int | None:
    try:
        from PIL import Image
        with Image.open(path) as image:
            grey = image.convert("L").resize((size + 1, size))
            pixels = list(grey.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(size):
        for column in range(size):
            left = pixels[row * (size + 1) + column]
            right = pixels[row * (size + 1) + column + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _symbols_only(text: str) -> str:
    return re.sub(r"\s+", "", text.replace(" ", " "))


def judge_text(expected: str, page_text: str) -> tuple[str, str | None]:
    """Characters, whitespace and symbols judged apart."""
    if expected in page_text:
        return "correct", None
    if _symbols_only(expected) in _symbols_only(page_text):
        expected_ws = re.findall(r"\s+", expected)
        detail = "characters kept, whitespace differs"
        if "\t" in expected and "\t" not in page_text:
            detail = "characters kept, tabs lost"
        elif "\n" in expected:
            detail = "characters kept, line breaks differ"
        elif any(len(run) > 1 for run in expected_ws):
            detail = "characters kept, repeated spaces collapsed"
        return "wrong", detail
    normalised = _norm_space(expected)
    if normalised and normalised in _norm_space(page_text):
        return "wrong", "symbols differ from the source"
    return "missing", "not found on this page"


@dataclass
class FigureVerdict:
    status: str
    detail: str
    evidence: dict


def judge_figure(source_object: dict, blocks: list[dict], *, asset_root: Path,
                  reference: Path | None, duplicate_hashes: set[str]) -> FigureVerdict:
    """A figure passes only if it is this object's own picture.

    Counting images on the page is not enough: a parser may return the same
    filler picture for every vector drawing, which looks like success and is not.
    A page can hold several pictures, so the candidate whose shape fits this
    source object is judged, not whichever one comes first.
    """
    candidates = [(b, b["assets"][0]) for b in blocks if b["kind"] == "figure" and b["assets"]]
    if not candidates:
        return FigureVerdict("missing", "no figure block with an asset on this page", {})

    scored = []
    for block, asset in candidates:
        aspect = _aspect_evidence(source_object, asset, asset_root)
        penalty = 0.0 if aspect.get("aspect_off") is False else (1.0 if aspect.get("aspect_off") else 0.5)
        scored.append((penalty, block, asset, aspect))
    scored.sort(key=lambda row: row[0])

    best: FigureVerdict | None = None
    for _, block, asset, aspect in scored:
        evidence = {"asset": asset.get("path"), "sha256": asset.get("sha256"), **aspect}
        path = asset_root / asset["path"] if asset.get("path") else None
        if path is None or not path.exists():
            candidate = FigureVerdict("wrong", "the asset file is not present", evidence)
        elif asset.get("decodable") is False:
            candidate = FigureVerdict("wrong", "the asset cannot be opened", evidence)
        elif asset.get("sha256") in duplicate_hashes:
            candidate = FigureVerdict(
                "wrong",
                "the same picture is handed out for several different source objects, so it is a filler, not this object's figure",
                evidence,
            )
        elif _is_flat(path):
            candidate = FigureVerdict("wrong", "the picture has no content: it is a flat area", evidence)
        elif aspect.get("aspect_off") is True:
            candidate = FigureVerdict(
                "wrong",
                f"aspect ratio {aspect['asset_aspect']} does not match the source object's {aspect['source_aspect']}; the picture is cropped, padded or not this object",
                evidence,
            )
        else:
            _add_reference_evidence(path, reference, evidence)
            if aspect.get("aspect_off") is False:
                candidate = FigureVerdict("correct", "the picture is this object's own, in the source object's shape", evidence)
            else:
                candidate = FigureVerdict(
                    "unknown", "the source object has no usable geometry, so the picture cannot be tied to it", evidence)
        if best is None or _rank(candidate.status) > _rank(best.status):
            best = candidate
        if best.status == "correct":
            break
    return best or FigureVerdict("missing", "no usable figure", {})


def _is_flat(path: Path) -> bool:
    """A blank or single-colour picture carries nothing, whatever its size."""
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as image:
            stat = ImageStat.Stat(image.convert("L"))
        return stat.stddev[0] < FLAT_IMAGE_STDDEV
    except Exception:
        return False


def _rank(status: str) -> int:
    return {"missing": 0, "wrong": 1, "unknown": 2, "correct": 3}.get(status, 0)


def _aspect_evidence(source_object: dict, asset: dict, asset_root: Path | None = None) -> dict:
    position = source_object.get("position_cm") or {}
    width, height = position.get("width"), position.get("height")
    asset_width, asset_height = asset.get("width"), asset.get("height")
    if (not asset_width or not asset_height) and asset_root is not None and asset.get("path"):
        # Docling's own asset record carries no size; read it from the file.
        try:
            from PIL import Image
            with Image.open(asset_root / asset["path"]) as image:
                asset_width, asset_height = image.size
        except Exception:
            pass
    if not (width and height and asset_width and asset_height):
        return {"aspect_off": None, "source_aspect": None, "asset_aspect": None}
    source_aspect = round(width / height, 3)
    asset_aspect = round(asset_width / asset_height, 3)
    off = abs(asset_aspect - source_aspect) / source_aspect > ASPECT_TOLERANCE
    return {"aspect_off": off, "source_aspect": source_aspect, "asset_aspect": asset_aspect}


def _add_reference_evidence(path: Path, reference: Path | None, evidence: dict) -> None:
    """Record how close the picture is to our preview crop. Evidence, not verdict."""
    if reference is None or not reference.exists():
        evidence["reference"] = "none available"
        return
    left, right = dhash(path), dhash(reference)
    if left is None or right is None:
        evidence["reference"] = "a picture could not be hashed"
        return
    distance = hamming(left, right)
    evidence["dhash_distance"] = distance
    evidence["reference"] = (
        "close to our preview crop" if distance <= DHASH_SUPPORTS
        else "differs from our preview crop, which is cut from a render and carries an offset"
    )


def judge_table(source_object: dict, blocks: list[dict], odp: Path) -> tuple[str, str | None, dict]:
    """Cell values and their row and column positions, not just the words."""
    expected = embedded_table(odp, source_object["href"]) if source_object.get("href") else None
    if not expected:
        return "unknown", "the source table could not be read", {}
    rows = expected["rows"]
    tables = [b for b in blocks if b["kind"] == "table"]
    evidence = {"source_rows": len(rows), "source_cells": sum(len(r) for r in rows)}
    if not tables:
        loose = "\n".join(b["content"] for b in blocks)
        found = sum(1 for row in rows for cell in row if cell and _norm_space(cell) in _norm_space(loose))
        evidence["cells_as_loose_text"] = found
        return "missing", f"no table block; {found} of {evidence['source_cells']} cell values appear as loose text", evidence

    parsed = [line.split("\t") for line in tables[0]["content"].splitlines()]
    evidence["parsed_rows"] = len(parsed)
    misplaced = []
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            if not cell.strip():
                continue
            actual = ""
            if row_index < len(parsed) and column_index < len(parsed[row_index]):
                actual = parsed[row_index][column_index]
            if _norm_space(actual) != _norm_space(cell):
                misplaced.append({"row": row_index, "column": column_index, "expected": cell, "actual": actual})
    evidence["misplaced"] = misplaced[:5]
    evidence["misplaced_count"] = len(misplaced)
    if not misplaced:
        return "correct", None, evidence
    return "wrong", f"{len(misplaced)} cells are missing or in the wrong row or column", evidence


def judge_source_binding(blocks: list[dict], slide_no: int) -> tuple[str, str]:
    """A block must name its source slide, and say plainly when it has no object or box."""
    if not blocks:
        return "missing", "the page produced no blocks"
    wrong_slide = [b for b in blocks if b["source_ref"].get("source_slide_no") != slide_no]
    if wrong_slide:
        return "wrong", f"{len(wrong_slide)} blocks name the wrong source slide"
    without_object = [b for b in blocks if b["source_ref"].get("mapping_status") != "mapped"]
    if without_object:
        return "missing", f"{len(without_object)} of {len(blocks)} blocks have no source object"
    return "correct", "every block names its source slide and object"


def duplicate_asset_hashes(normalized: dict) -> set[str]:
    """Hashes used by more than one figure block: the same picture cannot be two objects."""
    seen: dict[str, int] = {}
    for page in normalized["pages"]:
        for block in page["blocks"]:
            for asset in block["assets"]:
                digest = asset.get("sha256")
                if digest:
                    seen[digest] = seen.get(digest, 0) + 1
    return {digest for digest, count in seen.items() if count > 1}
