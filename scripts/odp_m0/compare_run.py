"""The MinerU round: run MinerU, re-normalise Docling fairly, judge both alike.

Two results are compared under one stricter check set:

1. MinerU native ODP.
2. Docling native ODP, re-normalised so that its inline groups are joined back
   into paragraphs. Docling is not re-run and no text is filled in from the ODF;
   only what Docling already returned is read properly.

The older "Docling + native supplement" result is carried as a reference point
only. It used extra extraction code of ours, so it is never ranked next to the
two plain parser results.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import checks_v2, compare_report, evaluate, mineru_normalize, mineru_probe, normalize, questions_v2, report
from .common import (
    ARTIFACT_ROOT, REPO_ROOT, RESULT_ROOT, SOURCE_DIR, M0Error, git_snapshot, new_run_dir,
    package_fingerprint, read_json, sha256_file, sha256_json, utc_now, write_json,
)

MINERU_PYTHON = "/tmp/odp-m0-mineru-env/bin/python"
PREVIOUS_ROOT = RESULT_ROOT
PREVIOUS_RUN = "2026-09-18-baseline-02"
RESULT_ROOT_V2 = REPO_ROOT / "documentation" / "ingestion-agent-acceptance" / "m0-mineru-native"


def build_check_set(suite: dict, inventory_by_deck: dict) -> dict:
    """The frozen 18 pages again, with the stricter judgements attached."""
    checks = []
    for page in suite["pages"]:
        deck, slide_no = page["deck"], page["source_slide_no"]
        record = inventory_by_deck[deck]
        source_page = record["pages"][slide_no - 1]
        prefix = f"{Path(deck).stem}-p{slide_no:02d}"
        shapes = [o for o in source_page["objects"] if o["role"] == "shape"]

        for obj in shapes:
            for position, line in enumerate(obj["text_sample"].splitlines(), start=1):
                if len(line.strip()) < 8:
                    continue
                checks.append({
                    "check_id": f"{prefix}-text-{len(checks) + 1:03d}", "deck": deck,
                    "source_slide_no": slide_no, "split": page["split"],
                    "category": "code_sql" if checks_v2.re.search(
                        r"\b(SELECT|FROM|WHERE|CREATE TABLE|INSERT INTO|GROUP BY|ORDER BY|HAVING|UPDATE)\b",
                        line, checks_v2.re.I) else "text_list",
                    "assertion": "text_exact_v2", "expected": line,
                    "source_object_path": obj["object_path"], "source_paragraph_no": position,
                    "verification": "automatic",
                })
        for obj in shapes:
            if obj["kind"] in {"embedded_graphics", "embedded_image", "group_shape"}:
                checks.append({
                    "check_id": f"{prefix}-figure-{len(checks) + 1:03d}", "deck": deck,
                    "source_slide_no": slide_no, "split": page["split"],
                    "category": "figure_embedded_image" if obj["kind"] == "embedded_image" else "figure_embedded_object",
                    "assertion": "figure_is_target_object", "expected": {"source_object_kind": obj["kind"]},
                    "source_object_path": obj["object_path"], "verification": "automatic",
                })
            if obj["kind"] == "embedded_spreadsheet":
                checks.append({
                    "check_id": f"{prefix}-table-{len(checks) + 1:03d}", "deck": deck,
                    "source_slide_no": slide_no, "split": page["split"], "category": "table",
                    "assertion": "table_cells_in_place", "expected": None,
                    "source_object_path": obj["object_path"], "verification": "automatic",
                })
        checks.append({
            "check_id": f"{prefix}-source-{len(checks) + 1:03d}", "deck": deck,
            "source_slide_no": slide_no, "split": page["split"], "category": "source_identity",
            "assertion": "source_binding_v2", "expected": None, "source_object_path": None,
            "verification": "automatic",
        })
        for category, assertion in (("reading_order", "blocks follow the order a student reads the slide in"),
                                     ("code_symbols", "code symbols and indentation are not damaged"),
                                     ("figure_meaning", "the figure keeps its meaning: directions and labels are not reversed")):
            if category == "code_symbols" and "code_sql" not in page["categories"]:
                continue
            if category == "figure_meaning" and not any(c.startswith("figure") for c in page["categories"]):
                continue
            checks.append({
                "check_id": f"{prefix}-human-{category}", "deck": deck, "source_slide_no": slide_no,
                "split": page["split"], "category": category, "assertion": assertion,
                "expected": None, "source_object_path": None, "verification": "human",
            })

    payload = {
        "generated_at": utc_now(), "frozen": True,
        "based_on_suite_sha256": suite["suite_sha256"],
        "note": ("Same 18 pages as the first round. They are a shared comparison set, not a fresh "
                 "held-out set: the held-out pages were already seen in the first round."),
        "check_count": len(checks),
        "automatic_count": sum(1 for c in checks if c["verification"] == "automatic"),
        "human_count": sum(1 for c in checks if c["verification"] == "human"),
        "checks": checks,
    }
    payload["checks_v2_sha256"] = sha256_json(checks)
    return payload


def _reference_crops(deck: str) -> dict:
    """Region crops from the first round: the source object's area in our preview."""
    path = PREVIOUS_ROOT / "runs" / PREVIOUS_RUN / Path(deck).stem / "normalized_supplement.json"
    if not path.exists():
        return {}
    data = read_json(path)
    out = {}
    for page in data["pages"]:
        for block in page["blocks"]:
            for asset in block["assets"]:
                if asset.get("asset_kind") == "rendered_region" and asset.get("source_object_path"):
                    out[asset["source_object_path"]] = Path(asset["path"])
    return out


def judge(check: dict, normalized: dict, record: dict, *, asset_root: Path, references: dict,
           duplicates: set, odp: Path) -> dict:
    page = next((p for p in normalized["pages"] if p["source_slide_no"] == check["source_slide_no"]), None)
    result = {
        "check_id": check["check_id"], "deck": check["deck"], "split": check["split"],
        "source_slide_no": check["source_slide_no"], "category": check["category"],
        "status": "unknown", "detail": None, "evidence": {}, "reviewer_kind": "automated",
    }
    if page is None:
        return dict(result, status="missing", detail="no page in the result for this slide")

    blocks = page["blocks"]
    page_text = "\n".join(b["content"] for b in blocks if b["content"])
    assertion = check["assertion"]

    if assertion == "text_exact_v2":
        status, detail = checks_v2.judge_text(check["expected"], page_text)
        result.update(status=status, detail=detail)
    elif assertion == "figure_is_target_object":
        obj = next((o for p in record["pages"] for o in p["objects"]
                    if o["object_path"] == check["source_object_path"]), None)
        verdict = checks_v2.judge_figure(
            obj or {}, blocks, asset_root=asset_root,
            reference=references.get(check["source_object_path"]), duplicate_hashes=duplicates)
        result.update(status=verdict.status, detail=verdict.detail, evidence=verdict.evidence)
    elif assertion == "table_cells_in_place":
        obj = next((o for p in record["pages"] for o in p["objects"]
                    if o["object_path"] == check["source_object_path"]), None)
        status, detail, evidence = checks_v2.judge_table(obj or {}, blocks, odp)
        result.update(status=status, detail=detail, evidence=evidence)
    elif assertion == "source_binding_v2":
        status, detail = checks_v2.judge_source_binding(blocks, check["source_slide_no"])
        result.update(status=status, detail=detail)
    return result


def judge_questions(questions: dict, normalized_by_deck: dict, inventory_by_deck: dict,
                     asset_roots: dict, references_by_deck: dict, duplicates_by_deck: dict) -> dict:
    results = []
    for question in questions["questions"]:
        entries = []
        for need in question["needs"]:
            deck = need["deck"]
            normalized = normalized_by_deck.get(deck)
            record = inventory_by_deck[deck]
            page = next((p for p in (normalized or {"pages": []})["pages"]
                         if p["source_slide_no"] == need["source_slide_no"]), None)
            blocks = page["blocks"] if page else []
            page_text = "\n".join(b["content"] for b in blocks if b["content"])
            text_results = []
            for required in need["required_texts"]:
                status, detail = checks_v2.judge_text(required["required_text"] or "", page_text)
                text_results.append({"anchor": required["anchor"], "status": status, "detail": detail})
            figure_status = "not_required"
            if need["required_figure"]:
                figure_status = "missing"
                for object_path in need["figure_objects"]:
                    obj = next((o for p in record["pages"] for o in p["objects"]
                                if o["object_path"] == object_path), None)
                    verdict = checks_v2.judge_figure(
                        obj or {}, blocks, asset_root=asset_roots.get(deck, Path(".")),
                        reference=references_by_deck.get(deck, {}).get(object_path),
                        duplicate_hashes=duplicates_by_deck.get(deck, set()))
                    if checks_v2._rank(verdict.status) > checks_v2._rank(figure_status):
                        figure_status = verdict.status
            table_status = "not_required"
            if need["required_table"]:
                table_status = "missing" if not [b for b in blocks if b["kind"] == "table"] else "correct"
            entries.append({
                "deck": deck, "source_slide_no": need["source_slide_no"],
                "texts": text_results, "figure": figure_status, "table": table_status,
            })
        text_ok = all(t["status"] == "correct" for e in entries for t in e["texts"])
        text_present = all(t["status"] in ("correct", "wrong") for e in entries for t in e["texts"])
        figure_ok = all(e["figure"] in ("correct", "not_required") for e in entries)
        table_ok = all(e["table"] in ("correct", "not_required") for e in entries)
        verdict = ("preserved" if text_ok and figure_ok and table_ok
                   else "text_damaged" if text_present and figure_ok and table_ok
                   else "incomplete")
        results.append({"question_id": question["question_id"], "entries": entries, "verdict": verdict})
    return {
        "generated_at": utc_now(),
        "questions_v2_sha256": questions["questions_v2_sha256"],
        "note": "Evidence presence and fidelity only. No agent is run and no answer accuracy is reported.",
        "results": results,
        "preserved": sum(1 for r in results if r["verdict"] == "preserved"),
        "text_damaged": sum(1 for r in results if r["verdict"] == "text_damaged"),
        "incomplete": sum(1 for r in results if r["verdict"] == "incomplete"),
    }


def summarise(results: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    by_category: dict[str, dict] = {}
    by_split: dict[str, dict] = {}
    for item in results:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        by_category.setdefault(item["category"], {}).setdefault(item["status"], 0)
        by_category[item["category"]][item["status"]] += 1
        by_split.setdefault(item["split"], {}).setdefault(item["status"], 0)
        by_split[item["split"]][item["status"]] += 1
    return {"by_status": by_status, "by_category": by_category, "by_split": by_split}


def run(run_id: str, *, mineru_python: str = MINERU_PYTHON) -> dict:
    started = time.time()
    run_dir = new_run_dir(run_id, RESULT_ROOT_V2)
    artifact_dir = ARTIFACT_ROOT / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    inventory = read_json(PREVIOUS_ROOT / "inventory.json")
    suite = read_json(PREVIOUS_ROOT / "suite.json")
    inventory_by_deck = {f["file_name"]: f for f in inventory["files"]}

    changed = [f["file_name"] for f in inventory["files"]
               if sha256_file(SOURCE_DIR / f["file_name"]) != f["sha256"]]
    if changed:
        raise M0Error(f"source files changed since the first round: {changed}")

    check_set = build_check_set(suite, inventory_by_deck)
    write_json(RESULT_ROOT_V2 / "checks_v2.json", check_set, overwrite=True)
    questions = questions_v2.build(inventory_by_deck)
    write_json(RESULT_ROOT_V2 / "questions_v2.json", questions, overwrite=True)

    environment = {
        "generated_at": utc_now(),
        "mineru": mineru_probe.environment_facts(mineru_python),
        "docling_first_round": read_json(PREVIOUS_ROOT / "runs" / PREVIOUS_RUN / "environment.json")["docling"],
        "package_fingerprint": package_fingerprint(),
        "git": git_snapshot(),
        "install_commands": [
            "python3 -m venv /tmp/odp-m0-mineru-env",
            "/tmp/odp-m0-mineru-env/bin/pip install mineru==4.0.2",
        ],
    }
    write_json(run_dir / "environment.json", environment)

    mineru_by_deck, docling_by_deck, per_deck = {}, {}, []
    references_by_deck, duplicates, asset_roots_mineru, asset_roots_docling = {}, {}, {}, {}

    for record in inventory["files"]:
        deck = record["file_name"]
        odp = SOURCE_DIR / deck
        deck_dir = run_dir / Path(deck).stem
        deck_dir.mkdir(parents=True)

        probe = mineru_probe.probe_file(odp, deck_dir / "mineru", python=mineru_python)
        write_json(deck_dir / "mineru_probe.json", probe)
        if probe["status"] != "ok":
            per_deck.append({"deck": deck, "mineru": probe})
            continue
        raw = read_json(deck_dir / "mineru" / "mineru_raw.json")
        mineru_norm = mineru_normalize.normalize_document(raw, record, probe, run_id=run_id)
        write_json(deck_dir / "normalized_mineru.json", mineru_norm)
        mineru_by_deck[deck] = mineru_norm
        asset_roots_mineru[deck] = deck_dir / "mineru"

        previous = PREVIOUS_ROOT / "runs" / PREVIOUS_RUN / Path(deck).stem
        docling_raw = read_json(previous / "docling_raw.json")
        docling_probe_data = read_json(previous / "probe.json")
        docling_norm = normalize.normalize_document(
            docling_raw, record, docling_probe_data, run_id=run_id, parser="docling-odp-native",
            parser_version=environment["docling_first_round"]["docling"], merge_inline=True,
            variant="docling-native-inline-merged")
        write_json(deck_dir / "normalized_docling_inline_merged.json", docling_norm)
        docling_by_deck[deck] = docling_norm
        asset_roots_docling[deck] = previous

        references_by_deck[deck] = _reference_crops(deck)
        duplicates[deck] = {
            "mineru": checks_v2.duplicate_asset_hashes(mineru_norm),
            "docling": checks_v2.duplicate_asset_hashes(docling_norm),
        }
        per_deck.append({
            "deck": deck,
            "mineru": {k: v for k, v in probe.items() if k in ("status", "seconds", "counts", "item_total", "page_count", "converter_warnings", "model_calls")},
            "mineru_unique_images": len({a["sha256"] for a in probe.get("assets", [])}),
            "mineru_images": len(probe.get("assets", [])),
        })

    outcomes = {}
    for label, normalized_by_deck, asset_roots, dup_key in (
        ("mineru", mineru_by_deck, asset_roots_mineru, "mineru"),
        ("docling_inline_merged", docling_by_deck, asset_roots_docling, "docling"),
    ):
        results, pending = [], []
        for check in check_set["checks"]:
            deck = check["deck"]
            if check["verification"] == "human":
                pending.append({**{k: check[k] for k in ("check_id", "deck", "source_slide_no", "category", "assertion", "split")},
                                "status": "awaiting_human_review"})
                continue
            normalized = normalized_by_deck.get(deck)
            if normalized is None:
                results.append({"check_id": check["check_id"], "deck": deck, "split": check["split"],
                                "source_slide_no": check["source_slide_no"], "category": check["category"],
                                "status": "unknown", "detail": "no result for this deck",
                                "evidence": {}, "reviewer_kind": "automated"})
                continue
            results.append(judge(check, normalized, inventory_by_deck[deck],
                                 asset_root=asset_roots.get(deck, Path(".")),
                                 references=references_by_deck.get(deck, {}),
                                 duplicates=duplicates.get(deck, {}).get(dup_key, set()),
                                 odp=SOURCE_DIR / deck))
        evaluation = {
            "generated_at": utc_now(), "run_id": run_id, "parser": label,
            "checks_v2_sha256": check_set["checks_v2_sha256"],
            "artifact_hashes": {deck: sha256_json(normalized_by_deck[deck]) for deck in normalized_by_deck},
            "automatic_results": results,
            "summary": summarise(results),
            "pending_human_checks": pending,
            "human_review_state": "awaiting_human_review",
            "results_sha256": sha256_json(results),
        }
        write_json(run_dir / f"evaluation_{label}.json", evaluation)
        question_result = judge_questions(questions, normalized_by_deck, inventory_by_deck,
                                           asset_roots, references_by_deck,
                                           {d: duplicates.get(d, {}).get(dup_key, set()) for d in normalized_by_deck})
        write_json(run_dir / f"questions_evidence_{label}.json", question_result)
        coverage_totals = {}
        for deck, normalized in normalized_by_deck.items():
            coverage_totals[deck] = evaluate.coverage(normalized, inventory_by_deck[deck])["totals"]
        write_json(run_dir / f"coverage_{label}.json", coverage_totals)
        outcomes[label] = {"evaluation": evaluation, "questions": question_result, "coverage": coverage_totals}

    pages_written = compare_report.build(
        run_dir / "comparison", suite, inventory_by_deck,
        {label: outcomes[label]["evaluation"] for label in outcomes},
        {"mineru": mineru_by_deck, "docling_inline_merged": docling_by_deck},
    )

    summary = {
        "run_id": run_id, "finished_at": utc_now(), "seconds": round(time.time() - started, 1),
        "comparison_pages": len(pages_written),
        "decks": per_deck,
        "comparison": {label: outcomes[label]["evaluation"]["summary"]["by_status"] for label in outcomes},
        "questions": {label: {k: outcomes[label]["questions"][k] for k in ("preserved", "text_damaged", "incomplete")}
                      for label in outcomes},
        "model_calls": 0,
        "human_review_state": "awaiting_human_review",
        "artifact_dir": str(artifact_dir),
    }
    write_json(run_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MinerU vs Docling on ODP, one strict check set")
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--mineru-python", default=MINERU_PYTHON)
    args = parser.parse_args(argv)
    summary = run(args.run_id, mineru_python=args.mineru_python)
    print(json.dumps({k: v for k, v in summary.items() if k != "decks"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
