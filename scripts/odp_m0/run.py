"""Orchestrator for work line A. One command produces one run directory.

Large artifacts (preview PDFs and every page image) go to /tmp/odp-m0-artifacts/<run_id>
and are recorded there by absolute path and hash. The repository keeps the JSON
results, the eighteen comparison pages and the previews those pages show.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import docling_probe, evaluate, inventory, normalize, render, report, suite as suite_module, supplement
from .common import (
    ARTIFACT_ROOT,
    RESULT_ROOT,
    SOURCE_DIR,
    M0Error,
    git_snapshot,
    new_run_dir,
    package_fingerprint,
    read_json,
    sha256_file,
    sha256_json,
    tool_version,
    utc_now,
    write_json,
)

M0_PYTHON_DEFAULT = "/tmp/odp-m0-env/bin/python"


def environment_facts(python: str) -> dict:
    return {
        "generated_at": utc_now(),
        "repo_python": sys.version.split()[0],
        "m0_python": python,
        "docling": docling_probe.environment_facts(python),
        "libreoffice_host": tool_version(["libreoffice", "--version"]),
        "libreoffice_container": subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", render.RENDER_IMAGE, "-c", "soffice --version"],
            capture_output=True, text=True, timeout=180,
        ).stdout.strip(),
        "poppler_pdftotext": tool_version(["pdftotext", "-v"]),
        "render_isolation": render.isolation_report(),
        "package_fingerprint": package_fingerprint(),
        "git": git_snapshot(),
        "install_commands": [
            "python3 -m venv /tmp/odp-m0-env",
            "/tmp/odp-m0-env/bin/pip install 'docling-slim[format-opendocument]==2.127.0' odfdo==3.26.0 pypdfium2==5.13.0 docling-parse==7.19.1 pytest",
            "docker build -f scripts/odp_m0/Dockerfile.render -t edullm-odp-m0-render:1 scripts/odp_m0",
        ],
    }


def freeze_inputs(source_dir: Path, result_root: Path) -> tuple[dict, dict, dict, dict]:
    """Write inventory, suite, checks and questions once, then reuse them."""
    inventory_path = result_root / "inventory.json"
    if inventory_path.exists():
        inventory_data = read_json(inventory_path)
    else:
        inventory_data = inventory.build_inventory(source_dir)
        write_json(inventory_path, inventory_data)

    suite_path, checks_path, questions_path = (
        result_root / "suite.json", result_root / "checks.json", result_root / "questions.json"
    )
    if suite_path.exists() and checks_path.exists():
        suite_data, checks_data = read_json(suite_path), read_json(checks_path)
    else:
        suite_data, checks_data = suite_module.build_suite(source_dir)
        write_json(suite_path, suite_data)
        write_json(checks_path, checks_data)
    if questions_path.exists():
        questions_data = read_json(questions_path)
    else:
        questions_data = suite_module.build_questions()
        write_json(questions_path, questions_data)
    return inventory_data, suite_data, checks_data, questions_data


def _verify_sources_unchanged(inventory_data: dict, source_dir: Path) -> list[dict]:
    problems = []
    for record in inventory_data["files"]:
        path = source_dir / record["file_name"]
        if not path.exists():
            problems.append({"file": record["file_name"], "problem": "missing"})
        elif sha256_file(path) != record["sha256"]:
            problems.append({"file": record["file_name"], "problem": "hash changed since the inventory"})
    return problems


def run(run_id: str, *, python: str, source_dir: Path = SOURCE_DIR, result_root: Path = RESULT_ROOT,
        decks: list[str] | None = None) -> dict:
    started = time.time()
    run_dir = new_run_dir(run_id, result_root)
    artifact_dir = ARTIFACT_ROOT / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    inventory_data, suite_data, checks_data, questions_data = freeze_inputs(source_dir, result_root)
    changed = _verify_sources_unchanged(inventory_data, source_dir)
    if changed:
        raise M0Error(f"source files changed since the inventory: {changed}")

    environment = environment_facts(python)
    write_json(run_dir / "environment.json", environment)
    if not (result_root / "environment.json").exists():
        write_json(result_root / "environment.json", environment)

    write_json(run_dir / "config.json", {
        "run_id": run_id,
        "started_at": utc_now(),
        "source_dir": str(source_dir),
        "m0_python": python,
        "artifact_dir": str(artifact_dir),
        "suite_sha256": suite_data["suite_sha256"],
        "checks_sha256": checks_data["checks_sha256"],
        "questions_sha256": questions_data["questions_sha256"],
        "package_fingerprint": environment["package_fingerprint"]["combined"],
        "model_calls": 0,
    })

    selected = [r for r in inventory_data["files"] if not decks or r["file_name"] in decks]
    inventory_by_deck = {r["file_name"]: r for r in selected}
    normalized_by_deck, supplement_by_deck, coverage_by_deck, previews_by_deck = {}, {}, {}, {}
    per_deck_records = []

    for record in selected:
        deck = record["file_name"]
        odp = source_dir / deck
        deck_dir = run_dir / Path(deck).stem
        deck_dir.mkdir(parents=True)
        deck_artifacts = artifact_dir / Path(deck).stem
        deck_artifacts.mkdir(parents=True, exist_ok=True)

        render_result = render.render_file(odp, deck_artifacts)
        mapping = render.verify_mapping(record, render_result)
        images = render.page_images(Path(render_result.pdf_path), deck_artifacts / "pages") if render_result.status == "ok" else []
        previews_by_deck[deck] = images

        probe = docling_probe.probe_file(odp, deck_dir, python=python)
        if probe["status"] != "ok":
            per_deck_records.append({"deck": deck, "render": render_result.dump(), "probe": probe})
            continue

        raw = json.loads((deck_dir / "docling_raw.json").read_text(encoding="utf-8"))
        normalized = normalize.normalize_document(
            raw, record, probe, run_id=run_id, parser="docling-odp-native",
            parser_version=environment["docling"]["docling"],
        )
        normalize.attach_preview_mapping(normalized, mapping["mapping"])
        write_json(deck_dir / "normalized.json", normalized)

        variant = supplement.build_supplement(
            normalized, record, odp, preview_images=images, asset_dir=deck_artifacts / "regions"
        )
        write_json(deck_dir / "normalized_supplement.json", variant)

        write_json(deck_dir / "render_mapping.json", {
            "render": render_result.dump(),
            "mapping": mapping,
            "preview_pdf": {
                "path": render_result.pdf_path,
                "sha256": render_result.pdf_sha256,
                "note": "large artifact kept outside the repository",
            },
            "page_images": [{k: v for k, v in image.items()} for image in images],
        })
        write_json(deck_dir / "probe.json", {k: v for k, v in probe.items() if k != "assets"} | {"assets": probe.get("assets", [])})

        normalized_by_deck[deck] = normalized
        supplement_by_deck[deck] = variant
        coverage_by_deck[deck] = evaluate.coverage(normalized, record)
        write_json(deck_dir / "coverage.json", coverage_by_deck[deck])
        write_json(deck_dir / "coverage_supplement.json", evaluate.coverage(variant, record))
        per_deck_records.append({"deck": deck, "render": render_result.dump(), "probe": {k: v for k, v in probe.items() if k in ("status", "seconds", "counts", "converter_warnings")}})

    artifact_hashes = {
        deck: {
            "normalized_sha256": sha256_json(normalized_by_deck[deck]),
            "supplement_sha256": sha256_json(supplement_by_deck[deck]),
        }
        for deck in normalized_by_deck
    }

    evaluation = evaluate.evaluate_run(
        checks_data, normalized_by_deck, inventory_by_deck, run_id=run_id, artifact_hashes=artifact_hashes
    )
    evaluation["split_summary"] = evaluate.split_summary(evaluation, suite_data)
    write_json(run_dir / "evaluation_baseline.json", evaluation)

    evaluation_supplement = evaluate.evaluate_run(
        checks_data, supplement_by_deck, inventory_by_deck, run_id=run_id, artifact_hashes=artifact_hashes
    )
    evaluation_supplement["split_summary"] = evaluate.split_summary(evaluation_supplement, suite_data)
    write_json(run_dir / "evaluation_supplement.json", evaluation_supplement)

    statistics = report.statistics(
        {"files": selected, "file_count": len(selected),
         "source_slide_total": sum(r["source_slide_count"] for r in selected),
         "hidden_slide_total": sum(r["hidden_slide_count"] for r in selected),
         "non_empty_notes_total": sum(r["non_empty_notes_count"] for r in selected)},
        normalized_by_deck, coverage_by_deck, evaluation, suite_data,
    )
    statistics["supplement_checks"] = evaluation_supplement["automatic_summary"]
    write_json(run_dir / "statistics.json", statistics)

    write_json(run_dir / "questions_evidence_baseline.json",
               report.questions_evidence(questions_data, normalized_by_deck, inventory_by_deck))
    write_json(run_dir / "questions_evidence_supplement.json",
               report.questions_evidence(questions_data, supplement_by_deck, inventory_by_deck))

    previews_for_pages = _copy_suite_previews(suite_data, previews_by_deck, normalized_by_deck, run_dir)
    written = report.build_comparison_pages(
        run_dir / "comparison", suite_data, inventory_by_deck, supplement_by_deck, evaluation_supplement, previews_for_pages
    )
    report.build_comparison_pages(
        run_dir / "comparison_baseline", suite_data, inventory_by_deck, normalized_by_deck, evaluation, previews_for_pages
    )

    summary = {
        "run_id": run_id,
        "finished_at": utc_now(),
        "seconds": round(time.time() - started, 1),
        "decks": per_deck_records,
        "comparison_pages": written,
        "artifact_dir": str(artifact_dir),
        "artifact_note": "preview PDFs and page images are not kept in the repository; rebuild with the command in README.md",
        "model_calls": 0,
        "human_review_state": "awaiting_human_review",
    }
    write_json(run_dir / "summary.json", summary)
    return summary


def _copy_suite_previews(suite_data: dict, previews_by_deck: dict, normalized_by_deck: dict, run_dir: Path) -> dict:
    """Copy only the previews the comparison pages show, so the repository stays small."""
    out: dict[str, list[dict]] = {}
    target_dir = run_dir / "comparison" / "previews"
    target_dir.mkdir(parents=True, exist_ok=True)
    for page in suite_data["pages"]:
        deck, slide_no = page["deck"], page["source_slide_no"]
        normalized = normalized_by_deck.get(deck)
        if not normalized:
            continue
        entry = next((p for p in normalized["pages"] if p["source_slide_no"] == slide_no), None)
        preview_no = entry.get("preview_pdf_page_no") if entry else None
        image = next((i for i in previews_by_deck.get(deck, []) if i["preview_pdf_page_no"] == preview_no), None)
        if not image:
            continue
        name = f"{Path(deck).stem}-p{slide_no:02d}.png"
        shutil.copy2(image["path"], target_dir / name)
        out.setdefault(deck, []).append(dict(image, relative_path=f"previews/{name}"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M0 ODP experiment, work line A")
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--python", default=M0_PYTHON_DEFAULT, help="the M0 environment's python")
    parser.add_argument("--source-dir", default=str(SOURCE_DIR))
    parser.add_argument("--result-root", default=str(RESULT_ROOT))
    parser.add_argument("--deck", action="append", help="limit the run to one deck; repeatable")
    args = parser.parse_args(argv)
    summary = run(args.run_id, python=args.python, source_dir=Path(args.source_dir),
                  result_root=Path(args.result_root), decks=args.deck)
    print(json.dumps({k: v for k, v in summary.items() if k != "decks"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
