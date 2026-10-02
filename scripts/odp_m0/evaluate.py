"""A4/A5 checking: run the frozen automatic checks against a normalised result.

Automatic checks can only produce the status "automated". They are literal
character checks, source and visibility checks, and hash binding. Whether the
meaning survived is a human question and stays in the pending list.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import sha256_json, utc_now

VERIFIED_MAPPING_CHECKS = {"anchor_unique_match", "word_overlap_unique_match"}


def _page_of(normalized: dict, slide_no: int) -> dict | None:
    return next((p for p in normalized["pages"] if p["source_slide_no"] == slide_no), None)


def _page_text(page: dict) -> str:
    return "\n".join(block["content"] for block in page["blocks"] if block["content"])


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def run_check(check: dict, normalized: dict, inventory_file: dict) -> dict:
    page = _page_of(normalized, check["source_slide_no"])
    result = {
        "check_id": check["check_id"],
        "deck": check["deck"],
        "source_slide_no": check["source_slide_no"],
        "category": check["category"],
        "importance": check["importance"],
        "status": "unknown",
        "detail": None,
        "reviewer_kind": "automated",
    }
    if page is None:
        result.update(status="missing", detail="no page in the normalised result for this slide")
        return result

    assertion = check["assertion"]
    text = _page_text(page)

    if assertion == "exact_text_present":
        expected = check["expected"]
        if expected in text:
            result["status"] = "correct"
        elif _normalise(expected) in _normalise(text):
            result.update(status="wrong", detail="text present but whitespace differs from the source")
        else:
            result.update(status="missing", detail="text not found on this page")

    elif assertion == "table_cells_present":
        tables = [b for b in page["blocks"] if b["kind"] == "table"]
        expected = check["expected"] or []
        if not tables:
            found = [value for value in expected if _normalise(value) in _normalise(text)]
            result.update(
                status="missing",
                detail=f"no table block; {len(found)} of {len(expected)} cell values appear as loose text",
            )
        else:
            table_text = _normalise("\n".join(b["content"] for b in tables))
            missing = [value for value in expected if _normalise(value) not in table_text]
            result.update(
                status="correct" if not missing else "wrong",
                detail=None if not missing else f"{len(missing)} of {len(expected)} cell values missing from the table block",
            )

    elif assertion == "figure_block_with_asset":
        figures = [b for b in page["blocks"] if b["kind"] == "figure"]
        with_asset = [b for b in figures if b["assets"]]
        if not figures:
            result.update(status="missing", detail="no figure block for this source object")
        elif not with_asset:
            result.update(status="wrong", detail="figure block has no stored asset")
        else:
            bound = [b for b in with_asset if any(a.get("source_object_path") for a in b["assets"])]
            result.update(
                status="correct" if bound else "wrong",
                detail=None if bound else "asset saved but not bound to a source object",
            )

    elif assertion == "every_block_has_source_object":
        blocks = page["blocks"]
        mapped = [b for b in blocks if b["source_ref"]["mapping_status"] == "mapped"]
        if not blocks:
            result.update(status="missing", detail="page has no blocks")
        elif len(mapped) == len(blocks):
            result["status"] = "correct"
        else:
            result.update(
                status="missing",
                detail=f"{len(blocks) - len(mapped)} of {len(blocks)} blocks have no source object",
            )

    elif assertion == "page_reference_resolves":
        if page.get("preview_pdf_page_no") is None:
            result.update(status="missing", detail="no preview page for this slide")
        elif page.get("preview_mapping_check") in VERIFIED_MAPPING_CHECKS:
            result["status"] = "correct"
        else:
            result.update(status="unknown", detail=f"mapping check is {page.get('preview_mapping_check')}; needs visual review")

    elif assertion == "no_notes_or_hidden_text_in_student_view":
        source_page = inventory_file["pages"][check["source_slide_no"] - 1]
        if not source_page["notes_non_empty"] and inventory_file["hidden_slide_count"] == 0:
            result.update(status="correct", detail="vacuous: this file has no notes text and no hidden slide")
        else:
            notes_lines = [
                line for line in (source_page.get("notes_text") or "").splitlines() if len(line.strip()) >= 8
            ]
            leaked = [line for line in notes_lines if _normalise(line) in _normalise(text)]
            result.update(
                status="wrong" if leaked else "correct",
                detail=f"{len(leaked)} notes lines appear in the student view" if leaked else None,
            )

    else:
        result.update(status="unknown", detail=f"no automatic implementation for {assertion}")
    return result


def coverage(normalized: dict, inventory_file: dict) -> dict:
    """How much of the source survived, counted from the source side."""
    per_page = []
    for source_page in inventory_file["pages"]:
        page = _page_of(normalized, source_page["source_slide_no"])
        text = _page_text(page) if page else ""
        normalised_text = _normalise(text)
        paragraphs = [
            line
            for obj in source_page["objects"]
            if obj["role"] == "shape"
            for line in obj["text_sample"].splitlines()
            if len(line.strip()) >= 8
        ]
        found = [p for p in paragraphs if _normalise(p) in normalised_text]
        exact = [p for p in paragraphs if p in text]
        # How often one source paragraph is spread over several blocks. Docling emits
        # one item per styled text run, which takes a code line apart.
        block_texts = [_normalise(b["content"]) for b in (page or {"blocks": []})["blocks"] if b["content"].strip()]
        split_paragraphs = 0
        for paragraph in paragraphs:
            target = _normalise(paragraph)
            if target and not any(target in block for block in block_texts):
                if target in normalised_text:
                    split_paragraphs += 1
        objects = [o for o in source_page["objects"] if o["role"] == "shape"]
        figures = [o for o in objects if o["kind"] in {"embedded_graphics", "embedded_image", "group_shape"}]
        tables = [o for o in objects if o["kind"] == "embedded_spreadsheet"]
        per_page.append({
            "source_slide_no": source_page["source_slide_no"],
            "source_paragraphs": len(paragraphs),
            "paragraphs_present": len(found),
            "paragraphs_character_exact": len(exact),
            "paragraphs_split_across_blocks": split_paragraphs,
            "source_figures": len(figures),
            "figure_blocks": len([b for b in (page or {"blocks": []})["blocks"] if b["kind"] == "figure"]),
            "source_tables": len(tables),
            "table_blocks": len([b for b in (page or {"blocks": []})["blocks"] if b["kind"] == "table"]),
            "blocks": len((page or {"blocks": []})["blocks"]),
        })
    totals = {
        key: sum(entry[key] for entry in per_page)
        for key in (
            "source_paragraphs", "paragraphs_present", "paragraphs_character_exact",
            "paragraphs_split_across_blocks",
            "source_figures", "figure_blocks", "source_tables", "table_blocks", "blocks",
        )
    }
    return {"per_page": per_page, "totals": totals}


def evaluate_run(check_set: dict, normalized_by_deck: dict, inventory_by_deck: dict, *, run_id: str, artifact_hashes: dict) -> dict:
    """Run every automatic check and collect the human ones as pending."""
    results = []
    pending_human = []
    for check in check_set["checks"]:
        deck = check["deck"]
        if check["verification"] == "human":
            pending_human.append({
                "check_id": check["check_id"],
                "deck": deck,
                "source_slide_no": check["source_slide_no"],
                "category": check["category"],
                "assertion": check["assertion"],
                "status": "awaiting_human_review",
            })
            continue
        normalized = normalized_by_deck.get(deck)
        inventory_file = inventory_by_deck.get(deck)
        if normalized is None or inventory_file is None:
            results.append({
                "check_id": check["check_id"], "deck": deck, "source_slide_no": check["source_slide_no"],
                "category": check["category"], "importance": check["importance"],
                "status": "unknown", "detail": "no result for this deck", "reviewer_kind": "automated",
            })
            continue
        results.append(run_check(check, normalized, inventory_file))

    by_status: dict[str, int] = {}
    by_category: dict[str, dict] = {}
    for result in results:
        by_status[result["status"]] = by_status.get(result["status"], 0) + 1
        bucket = by_category.setdefault(result["category"], {})
        bucket[result["status"]] = bucket.get(result["status"], 0) + 1

    return {
        "generated_at": utc_now(),
        "run_id": run_id,
        "checks_sha256": check_set["checks_sha256"],
        "artifact_hashes": artifact_hashes,
        "automatic_results": results,
        "automatic_summary": {"by_status": by_status, "by_category": by_category},
        "pending_human_checks": pending_human,
        "human_review_state": "awaiting_human_review",
        "results_sha256": sha256_json(results),
    }


def bind_review(evaluation: dict, *, reviewer: str, reviewer_kind: str, findings: list[dict]) -> dict:
    """A review is tied to the artifacts it looked at.

    reviewer_kind is human or model. An implementing agent records findings as
    "model" or "automated"; it must never sign as a human.
    """
    if reviewer_kind not in {"human", "model", "automated"}:
        raise ValueError("reviewer_kind must be human, model or automated")
    return {
        "generated_at": utc_now(),
        "run_id": evaluation["run_id"],
        "reviewer": reviewer,
        "reviewer_kind": reviewer_kind,
        "checks_sha256": evaluation["checks_sha256"],
        "results_sha256": evaluation["results_sha256"],
        "artifact_hashes": evaluation["artifact_hashes"],
        "findings": findings,
    }


def review_is_valid(review: dict, evaluation: dict) -> tuple[bool, str]:
    """A review stops counting as soon as the artifacts under it change."""
    if review["checks_sha256"] != evaluation["checks_sha256"]:
        return False, "the check set changed since this review"
    if review["results_sha256"] != evaluation["results_sha256"]:
        return False, "the results changed since this review"
    if review["artifact_hashes"] != evaluation["artifact_hashes"]:
        return False, "the run artifacts changed since this review"
    return True, "review matches the current artifacts"


def split_summary(evaluation: dict, suite: dict) -> dict:
    """Development and held-out pages are reported apart, never merged into one score."""
    split_by_page = {(p["deck"], p["source_slide_no"]): p["split"] for p in suite["pages"]}
    out: dict[str, dict] = {}
    for result in evaluation["automatic_results"]:
        split = split_by_page.get((result["deck"], result["source_slide_no"]), "unknown")
        bucket = out.setdefault(split, {})
        bucket[result["status"]] = bucket.get(result["status"], 0) + 1
    return out
