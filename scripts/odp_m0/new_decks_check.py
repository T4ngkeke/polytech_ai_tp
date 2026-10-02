"""Check the later uploads: hidden slides, native tables, figures, text.

These four decks arrived after the first two rounds were scored. They bring two
things the earlier eight did not have: slides that are really hidden, and tables
written straight into the slide rather than embedded as a spreadsheet object.
That makes them the first real test of D02 on actual courseware.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import docling_probe, inventory, mineru_probe
from .common import (ARTIFACT_ROOT, RESULT_ROOT, SOURCE_DIR, new_run_dir, sha256_file,
                      sha256_json, utc_now, write_json)

DECKS = [
    "StructureDonnees_GrapheEtArbre_2025-2026.odp",
    "Theorie_Des_Langages_2022-2023.odp",
    "Theorie_Des_Langages_2023-2024.odp",
    "Theorie_Des_Langages_2024-2025.odp",
]
RESULT_ROOT_V4 = RESULT_ROOT.parent / "m0-new-decks"


def hidden_only_text(record: dict) -> list[str]:
    """Lines that appear on hidden slides and nowhere else."""
    hidden, visible = set(), set()
    for page in record["pages"]:
        bucket = hidden if page["visibility"] == "hidden" else visible
        for obj in page["objects"]:
            if obj["role"] != "shape":
                continue
            for line in obj["text_sample"].splitlines():
                if len(line.strip()) >= 12:
                    bucket.add(line.strip())
    return sorted(hidden - visible)


def check_deck(deck: str, run_dir: Path, *, docling_python: str, mineru_python: str) -> dict:
    odp = SOURCE_DIR / deck
    record = inventory.inspect_file(odp)
    out = run_dir / Path(deck).stem
    secret = hidden_only_text(record)

    docling = docling_probe.probe_file(odp, out / "docling", python=docling_python)
    mineru = mineru_probe.probe_file(odp, out / "mineru", python=mineru_python)

    result = {
        "deck": deck,
        "sha256": record["sha256"],
        "source_slides": record["source_slide_count"],
        "visible_slides": record["visible_slide_count"],
        "hidden_slides": [p["source_slide_no"] for p in record["pages"] if p["visibility"] == "hidden"],
        "hidden_only_lines": len(secret),
        "native_tables": record["object_totals"].get("table", 0),
        "embedded_spreadsheets": record["object_totals"].get("embedded_spreadsheet", 0),
        "source_figures": sum(record["object_totals"].get(k, 0)
                              for k in ("embedded_graphics", "embedded_image", "group_shape")),
        "parsers": {},
    }

    for name, probe, text_file in (("docling", docling, "docling.md"), ("mineru", mineru, "mineru.md")):
        entry = {"status": probe["status"]}
        if probe["status"] == "ok":
            text = (out / name / text_file).read_text(encoding="utf-8")
            leaked = [line for line in secret if line in text]
            raw = json.loads((out / name / ("docling_raw.json" if name == "docling" else "mineru_raw.json"))
                             .read_text(encoding="utf-8"))
            if name == "docling":
                slides = len([g for g in raw.get("groups") or [] if (g.get("name") or "").startswith("slide-")])
                tables = len(raw.get("tables") or [])
                pictures = len(raw.get("pictures") or [])
            else:
                slides = len(raw["pages"])
                tables = sum(1 for p in raw["pages"] for i in p if str(i.get("type")).lower().endswith("table"))
                pictures = sum(1 for p in raw["pages"] for i in p if str(i.get("type")).lower().endswith("image"))
            unique_pictures = len({a["sha256"] for a in probe.get("assets", [])}) if name == "mineru" else None
            entry.update({
                "slides_returned": slides,
                "hidden_lines_leaked": len(leaked),
                "respects_hidden_slides": len(leaked) == 0,
                "tables_returned": tables,
                "pictures_returned": pictures,
                "unique_pictures": unique_pictures,
                "seconds": probe.get("seconds"),
            })
        result["parsers"][name] = entry

    # The raw results carry embedded images and three of these decks are nearly
    # identical, so the bulk goes outside the repository. The readable Markdown
    # stays, because the hidden slide check is read from it.
    moved = []
    artifacts = ARTIFACT_ROOT / run_dir.name / Path(deck).stem
    for name, raw_name in (("docling", "docling_raw.json"), ("mineru", "mineru_raw.json")):
        source_path = out / name / raw_name
        if not source_path.exists():
            continue
        target = artifacts / name / raw_name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(source_path)
        source_path.replace(target)
        moved.append({"file": f"{name}/{raw_name}", "kept_at": str(target), "sha256": digest})
        assets = out / name / "assets"
        if assets.exists():
            asset_target = artifacts / name / "assets"
            asset_target.parent.mkdir(parents=True, exist_ok=True)
            if asset_target.exists():
                import shutil
                shutil.rmtree(asset_target)
            assets.replace(asset_target)
            moved.append({"file": f"{name}/assets", "kept_at": str(asset_target), "sha256": None})
    result["large_artifacts"] = {
        "note": "not kept in the repository; rebuild with new_decks_check under a new run id",
        "moved": moved,
    }
    write_json(out / "check.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the later uploads")
    parser.add_argument("--run-id", default=utc_now().replace(":", "").replace("-", ""))
    parser.add_argument("--docling-python", default="/tmp/odp-m0-env/bin/python")
    parser.add_argument("--mineru-python", default="/tmp/odp-m0-mineru-env/bin/python")
    args = parser.parse_args(argv)

    run_dir = new_run_dir(args.run_id, RESULT_ROOT_V4)
    results = [check_deck(deck, run_dir, docling_python=args.docling_python,
                          mineru_python=args.mineru_python) for deck in DECKS]
    summary = {
        "generated_at": utc_now(), "run_id": args.run_id,
        "note": ("Decks uploaded after the first two rounds were scored. The three Theorie des "
                 "langages files are the same course in three years and are nearly identical, so "
                 "they are three versions of one sample, not three independent ones."),
        "decks": results,
        "totals": {
            "hidden_slides": sum(len(r["hidden_slides"]) for r in results),
            "docling_leaks_hidden": sum(1 for r in results
                                        if r["parsers"]["docling"].get("respects_hidden_slides") is False),
            "mineru_leaks_hidden": sum(1 for r in results
                                       if r["parsers"]["mineru"].get("respects_hidden_slides") is False),
            "native_tables": sum(r["native_tables"] for r in results),
            "docling_tables": sum(r["parsers"]["docling"].get("tables_returned", 0) for r in results),
            "mineru_tables": sum(r["parsers"]["mineru"].get("tables_returned", 0) for r in results),
            "source_figures": sum(r["source_figures"] for r in results),
            "mineru_unique_pictures": sum(r["parsers"]["mineru"].get("unique_pictures") or 0 for r in results),
            "mineru_pictures": sum(r["parsers"]["mineru"].get("pictures_returned", 0) for r in results),
            "docling_pictures": sum(r["parsers"]["docling"].get("pictures_returned", 0) for r in results),
        },
    }
    summary["results_sha256"] = sha256_json(results)
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary["totals"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
