"""Run methods A, B and C over the frozen figure set and score every fact."""
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from . import figure_facts, figure_render, vision_run, vision_score, vision_suite
from .common import (ARTIFACT_ROOT, REPO_ROOT, RESULT_ROOT, SOURCE_DIR, M0Error, git_snapshot,
                     new_run_dir, package_fingerprint, read_json, sha256_file, sha256_json,
                     utc_now, write_json)
from .supplement import embedded_table

RESULT_ROOT_V3 = REPO_ROOT / "documentation" / "ingestion-agent-acceptance" / "m0-vision-benefit"
# Page previews come from earlier render runs. Courseware uploaded later has its
# own render, so every known location is searched and a missing preview is an
# error rather than a silent fallback: without it method C is not method C.
PREVIEW_ROOTS = [ARTIFACT_ROOT / "2026-09-18-baseline-02",
                 ARTIFACT_ROOT / "2026-09-18-new-decks-pages"]
ROUNDS = 2


def build_inputs(run_dir: Path, inventory_by_deck: dict, targets_spec=None) -> dict:
    """The shared input every method uses. Built before any model call."""
    figures_dir = run_dir / "figures"
    work = ARTIFACT_ROOT / run_dir.name / "figure_work"
    targets = []
    for deck, slide, tail, category, why in (targets_spec or vision_suite.TARGETS):
        record = inventory_by_deck[deck]
        obj = vision_suite.find_object(record, slide, tail)
        identifier = vision_suite.target_id(deck, slide, tail)
        page = record["pages"][slide - 1]
        odp = SOURCE_DIR / deck

        figure = (figure_render.render_object(odp, slide, obj["object_path"], work / identifier,
                                              figures_dir / f"{identifier}.png")
                  if obj else {"status": "failed", "reason": "object not found on that slide"})
        facts = figure_facts.facts_for(odp, slide, obj["object_path"]) if obj else {"status": "no_object"}

        usable = figure.get("status") == "ok" and facts.get("status") == "ok"
        reason = None
        if figure.get("status") != "ok":
            reason = f"figure extraction: {figure.get('reason')}"
        elif facts.get("status") != "ok":
            reason = f"source facts: {facts.get('reason', facts.get('status'))}"
        elif facts.get("node_count", 0) == 0:
            usable, reason = False, "no nodes could be read from the source drawing"
        elif facts.get("unmatched_edges", 0) > 0:
            usable, reason = False, (
                f"{facts['unmatched_edges']} connectors could not be tied to two nodes, so our own "
                "fact list is incomplete; judging the model against it would test our extraction")

        preview = _page_preview(deck, slide)
        if preview:
            shutil.copy2(preview, run_dir / "pages" / f"{identifier}-page.png")
        elif usable:
            # Method C is defined by the page image. Without it C would quietly be
            # a second run of B, and the comparison would mean nothing.
            usable = False
            reason = "no page preview for this slide, so method C cannot be run"

        targets.append({
            "target_id": identifier, "deck": deck, "source_slide_no": slide,
            "category": category, "why_chosen": why,
            "source_object_path": obj["object_path"] if obj else None,
            "slide_title": page["title_candidate"],
            "native_text_lines": [line for o in page["objects"] if o["role"] == "shape"
                                   for line in o["text_sample"].splitlines() if line.strip()],
            "figure": figure,
            "facts": facts,
            "page_preview": str(run_dir / "pages" / f"{identifier}-page.png") if preview else None,
            "usable_for_scoring": usable,
            "not_usable_reason": reason,
            # If many connectors could not be tied to two nodes, our own fact list is
            # incomplete. Such a figure is scored on structure only: judging its edges
            # would test our extraction, not the model.
            "labelled": (bool(facts.get("labels_present")) and facts.get("unmatched_edges", 0) == 0
                          and not facts.get("duplicate_labels"))
                        if facts.get("status") == "ok" else False,
            "countable": (bool(facts.get("labels_present")) and facts.get("unmatched_edges", 0) == 0)
                         if facts.get("status") == "ok" else False,
            "duplicate_labels": bool(facts.get("duplicate_labels")) if facts.get("status") == "ok" else False,
            "facts_complete": facts.get("unmatched_edges", 0) == 0 if facts.get("status") == "ok" else False,
        })

    protected = []
    for deck, slide in vision_suite.PROTECTED_CODE:
        page = inventory_by_deck[deck]["pages"][slide - 1]
        lines = [line for o in page["objects"] if o["role"] == "shape"
                 for line in o["text_sample"].splitlines() if line.strip()]
        protected.append({"kind": "code", "deck": deck, "source_slide_no": slide,
                          "lines": lines, "sha256": sha256_json(lines)})
    for deck, slide in vision_suite.PROTECTED_TABLES:
        page = inventory_by_deck[deck]["pages"][slide - 1]
        obj = next((o for o in page["objects"] if o["kind"] == "embedded_spreadsheet"), None)
        table = embedded_table(SOURCE_DIR / deck, obj["href"]) if obj and obj.get("href") else None
        rows = table["rows"] if table else None
        protected.append({"kind": "table", "deck": deck, "source_slide_no": slide,
                          "rows": rows, "sha256": sha256_json(rows)})

    manifest = {
        "generated_at": utc_now(), "frozen": True,
        "note": ("Shared input for A, B and C, built from the ODP and our own single object render "
                 "before any model call. A figure that could not be extracted cleanly is marked "
                 "not usable and does not judge the model."),
        "targets": targets, "protected": protected,
    }
    manifest["inputs_sha256"] = sha256_json([
        {"id": t["target_id"], "figure": t["figure"].get("sha256"), "usable": t["usable_for_scoring"]}
        for t in targets])
    return manifest


def _page_preview(deck: str, slide: int) -> Path | None:
    stem = Path(deck).stem
    for root in PREVIEW_ROOTS:
        for name in (f"page-{slide:02d}.png", f"page-{slide}.png"):
            candidate = root / stem / "pages" / name
            if candidate.exists():
                return candidate
    return None


def run(run_id: str, *, endpoint: str, model: str, api_key: str = "", targets_spec=None) -> dict:
    started = time.time()
    run_dir = new_run_dir(run_id, RESULT_ROOT_V3)
    (run_dir / "figures").mkdir(parents=True, exist_ok=True)
    (run_dir / "pages").mkdir(parents=True, exist_ok=True)

    inventory = read_json(RESULT_ROOT / "inventory.json")
    inventory_by_deck = {f["file_name"]: f for f in inventory["files"]}
    changed = [f["file_name"] for f in inventory["files"]
               if sha256_file(SOURCE_DIR / f["file_name"]) != f["sha256"]]
    if changed:
        raise M0Error(f"source files changed: {changed}")

    # Courseware added after the first inventory was frozen is inspected here and
    # its hashes are written into this run. The first inventory is not touched.
    from .inventory import inspect_file
    added = {}
    for deck, _, _, _, _ in (targets_spec or vision_suite.TARGETS):
        if deck not in inventory_by_deck:
            record = inspect_file(SOURCE_DIR / deck)
            if record["status"] != "ok":
                raise M0Error(f"{deck}: {record.get('reason')}")
            inventory_by_deck[deck] = record
            added[deck] = {"sha256": record["sha256"], "size_bytes": record["size_bytes"],
                           "source_slides": record["source_slide_count"],
                           "hidden_slides": record["hidden_slide_count"],
                           "object_totals": record["object_totals"]}
    if added:
        write_json(run_dir / "inventory_added.json", {
            "generated_at": utc_now(),
            "note": "decks uploaded after the first inventory was frozen; that file was not modified",
            "decks": added})

    manifest = build_inputs(run_dir, inventory_by_deck, targets_spec)
    write_json(run_dir / "inputs.json", manifest)

    write_json(run_dir / "environment.json", {
        "generated_at": utc_now(), "endpoint": endpoint, "model": model,
        "endpoint_note": ("the deployment configuration names port 8001, which has no listener; "
                          "this is the same ornith model served on 18001. No deployment config was changed."),
        "rounds": ROUNDS, "package_fingerprint": package_fingerprint(), "git": git_snapshot(),
        "decoding": "response_format json_schema, strict; temperature 0; max_tokens 700",
    })

    calls, results = [], []
    for target in manifest["targets"]:
        entry = {"target_id": target["target_id"], "category": target["category"],
                 "usable_for_scoring": target["usable_for_scoring"],
                 "not_usable_reason": target["not_usable_reason"],
                 "labelled": target["labelled"], "methods": {}}
        entry["methods"]["A"] = {"rounds": [vision_run.method_a(target)],
                                  "scored": None,
                                  "note": "no model call, so no relation is claimed and none can be wrong"}
        if target["figure"].get("status") == "ok":
            figure_path = Path(target["figure"]["path"])
            page_path = Path(target["page_preview"]) if target["page_preview"] else None
            for method, page_image in (("B", None), ("C", page_path)):
                rounds = []
                for round_index in range(1, ROUNDS + 1):
                    record = vision_run.ask(endpoint, model, api_key, figure_path, page_image)
                    record["round"] = round_index
                    record["method"] = method
                    record["target_id"] = target["target_id"]
                    calls.append({k: v for k, v in record.items() if k != "answer"})
                    scored = None
                    if record.get("status") == "ok" and target["usable_for_scoring"]:
                        scored = vision_score.score_reading(target["facts"], record.get("answer"),
                                                            labelled=target["labelled"],
                                                            countable=target.get("countable"))
                        scored["uncertainty"] = vision_score.flagged_uncertainty(record.get("answer"), scored)
                    rounds.append({"round": round_index, "status": record.get("status"),
                                    "seconds": record.get("seconds"), "usage": record.get("usage"),
                                    "answer": record.get("answer"), "scored": scored})
                entry["methods"][method] = {"rounds": rounds}
        else:
            for method in ("B", "C"):
                entry["methods"][method] = {"rounds": [], "skipped": target["not_usable_reason"]}
        results.append(entry)

    write_json(run_dir / "results.json", {"generated_at": utc_now(), "run_id": run_id, "targets": results})
    write_json(run_dir / "calls.json", {"generated_at": utc_now(), "call_count": len(calls), "calls": calls})

    protection = check_protected(manifest, inventory_by_deck)
    write_json(run_dir / "protection.json", protection)

    summary = summarise(results, calls, protection)
    summary.update(run_id=run_id, finished_at=utc_now(), seconds=round(time.time() - started, 1),
                    human_review_state="awaiting_human_review")
    write_json(run_dir / "summary.json", summary)
    return summary


def check_protected(manifest: dict, inventory_by_deck: dict) -> dict:
    """The code and tables must be untouched by the vision round."""
    out = []
    for item in manifest["protected"]:
        page = inventory_by_deck[item["deck"]]["pages"][item["source_slide_no"] - 1]
        if item["kind"] == "code":
            lines = [line for o in page["objects"] if o["role"] == "shape"
                     for line in o["text_sample"].splitlines() if line.strip()]
            same = sha256_json(lines) == item["sha256"]
        else:
            obj = next((o for o in page["objects"] if o["kind"] == "embedded_spreadsheet"), None)
            table = embedded_table(SOURCE_DIR / item["deck"], obj["href"]) if obj and obj.get("href") else None
            same = sha256_json(table["rows"] if table else None) == item["sha256"]
        out.append({"kind": item["kind"], "deck": item["deck"], "source_slide_no": item["source_slide_no"],
                    "unchanged": same})
    return {"generated_at": utc_now(), "all_unchanged": all(x["unchanged"] for x in out), "items": out}


def summarise(results: list[dict], calls: list[dict], protection: dict) -> dict:
    per_method: dict[str, dict] = {}
    for method in ("A", "B", "C"):
        totals = {"correct": 0, "missing": 0, "wrong": 0, "invented": 0, "checks": 0,
                  "readings": 0, "failed_calls": 0, "silent_errors": 0, "said_unsure": 0,
                  "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0}
        for target in results:
            if not target["usable_for_scoring"]:
                continue
            block = target["methods"].get(method, {})
            for reading in block.get("rounds", []):
                if method == "A":
                    continue
                if reading.get("status") != "ok":
                    totals["failed_calls"] += 1
                    continue
                scored = reading.get("scored")
                if not scored:
                    continue
                totals["readings"] += 1
                for key in ("correct", "missing", "wrong", "invented"):
                    totals[key] += scored[key]
                totals["checks"] += scored["total"]
                uncertainty = scored.get("uncertainty") or {}
                totals["silent_errors"] += 1 if uncertainty.get("silent_errors") else 0
                totals["said_unsure"] += 1 if uncertainty.get("said_unsure") else 0
                totals["seconds"] += reading.get("seconds") or 0
                usage = reading.get("usage") or {}
                totals["prompt_tokens"] += usage.get("prompt_tokens") or 0
                totals["completion_tokens"] += usage.get("completion_tokens") or 0
        totals["seconds"] = round(totals["seconds"], 1)
        per_method[method] = totals

    holdout = [t["target_id"] for t in results if t["category"] == "holdout"]
    return {
        "targets": len(results),
        "usable_targets": sum(1 for t in results if t["usable_for_scoring"]),
        "unusable_targets": [{"target_id": t["target_id"], "reason": t["not_usable_reason"]}
                              for t in results if not t["usable_for_scoring"]],
        "holdout_targets": holdout,
        "per_method": per_method,
        "development_vs_holdout": _split(results),
        "vision_calls": len(calls),
        "protection_all_unchanged": protection["all_unchanged"],
    }


def _split(results: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for target in results:
        if not target["usable_for_scoring"]:
            continue
        split = "holdout" if target["category"] in ("holdout",) or target.get("holdout_v2") else "development"
        for method in ("B", "C"):
            bucket = out.setdefault(split, {}).setdefault(method, {"correct": 0, "missing": 0, "wrong": 0, "invented": 0})
            for reading in target["methods"].get(method, {}).get("rounds", []):
                scored = reading.get("scored")
                if not scored:
                    continue
                for key in bucket:
                    bucket[key] += scored[key]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Does reading the figure with a model help?")
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:18001/v1")
    parser.add_argument("--model", default="ornith")
    parser.add_argument("--holdout-v2", action="store_true",
                        help="use the second held-out set, from the later uploads")
    args = parser.parse_args(argv)
    summary = run(args.run_id, endpoint=args.endpoint, model=args.model,
                  targets_spec=vision_suite.HOLDOUT_V2 if args.holdout_v2 else None)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def rescore(run_id: str) -> dict:
    """Score an existing run again after a scoring fault was fixed.

    No model is called: the stored answers are read again. Fixing how an answer
    is judged is not the same as asking for another answer, and the call count
    stays where it was.
    """
    run_dir = RESULT_ROOT_V3 / "runs" / run_id
    results = read_json(run_dir / "results.json")
    manifest = read_json(run_dir / "inputs.json")
    facts_by_target = {t["target_id"]: t for t in manifest["targets"]}

    for target in results["targets"]:
        source = facts_by_target[target["target_id"]]
        facts = source["facts"]
        target["usable_for_scoring"] = source["usable_for_scoring"]
        target["not_usable_reason"] = source["not_usable_reason"]
        target["labelled"] = source["labelled"]
        for method in ("B", "C"):
            for reading in target["methods"].get(method, {}).get("rounds", []):
                if reading.get("status") != "ok" or not target["usable_for_scoring"]:
                    reading["scored"] = None
                    continue
                scored = vision_score.score_reading(facts, reading.get("answer"), labelled=target["labelled"],
                                                     countable=source.get("countable"))
                scored["uncertainty"] = vision_score.flagged_uncertainty(reading.get("answer"), scored)
                reading["scored"] = scored

    calls = read_json(run_dir / "calls.json")["calls"]
    protection = read_json(run_dir / "protection.json")
    write_json(run_dir / "results.json", results, overwrite=True)
    summary = summarise(results["targets"], calls, protection)
    summary.update(run_id=run_id, finished_at=utc_now(), human_review_state="awaiting_human_review",
                    rescored=True,
                    rescore_note="answers reused; only the judging was corrected, no new model call")
    write_json(run_dir / "summary.json", summary, overwrite=True)
    return summary
