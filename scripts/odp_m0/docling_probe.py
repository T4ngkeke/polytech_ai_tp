"""A3: the Docling native ODP baseline.

Docling routes ODP through SimplePipeline, which simply calls the declarative
backend, so the backend is driven directly here. No PDF pipeline options, no
OCR, no model download: the M0 environment has no ML dependency installed at
all, which is itself the evidence that this path calls nothing remote.

Each file is converted once, in its own child process, with a timeout. A run
that times out leaves no successful artifact behind.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from .common import M0Error, sha256_bytes, sha256_file, utc_now, write_json

PROBE_TIMEOUT_SECONDS = 120


def environment_facts(python: str) -> dict:
    """Versions and code fingerprints that a result must be read against."""
    code = (
        "import json,sys;"
        "from importlib.metadata import version as _v;"
        "import docling, docling_core;"
        "from docling.backend import opendocument_backend as ob;"
        "import odfdo;"
        "print(json.dumps({"
        "'python': sys.version.split()[0],"
        "'docling': _v('docling-slim'),"
        "'docling_core': _v('docling-core'),"
        "'odfdo': _v('odfdo'),"
        "'backend_module': ob.__file__,"
        "}))"
    )
    result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise M0Error(f"cannot read the Docling environment: {result.stderr[-500:]}")
    facts = json.loads(result.stdout)
    facts["backend_sha256"] = sha256_file(Path(facts["backend_module"]))
    facts["ml_dependencies_installed"] = _has_ml_dependencies(python)
    return facts


def _has_ml_dependencies(python: str) -> list[str]:
    code = (
        "import importlib.util, json;"
        "print(json.dumps([m for m in ['torch','transformers','docling_ibm_models','easyocr']"
        " if importlib.util.find_spec(m) is not None]))"
    )
    result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
    return json.loads(result.stdout) if result.returncode == 0 else ["unknown"]


def _convert_in_child(source: Path, out_dir: Path) -> dict:
    """Runs inside the child process: convert, save the raw result and the assets."""
    warnings: list[dict] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append({"logger": record.name, "level": record.levelname, "message": record.getMessage()})

    handler = _Collect()
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    from docling.backend.opendocument_backend import OdpDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    started = time.time()
    input_doc = InputDocument(
        path_or_stream=source, format=InputFormat.ODP, backend=OdpDocumentBackend, filename=source.name
    )
    backend = input_doc._backend
    page_count = backend.page_count()
    document = backend.convert()
    elapsed = time.time() - started

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = document.export_to_dict()
    (out_dir / "docling_raw.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "docling.md").write_text(document.export_to_markdown(), encoding="utf-8")

    assets = []
    asset_dir = out_dir / "assets"
    for index, picture in enumerate(document.pictures):
        image = getattr(picture, "image", None)
        pil = getattr(image, "pil_image", None) if image is not None else None
        if pil is None:
            assets.append({"item_ref": picture.self_ref, "status": "no_image_data"})
            continue
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / f"picture-{index:03d}.png"
        pil.save(target)
        assets.append({
            "item_ref": picture.self_ref,
            "path": str(target.relative_to(out_dir)),
            "sha256": sha256_file(target),
            "mime": "image/png",
            "width": pil.width,
            "height": pil.height,
            "asset_kind": "original",
        })

    items_with_prov = sum(1 for item, _ in document.iterate_items() if getattr(item, "prov", None))
    total_items = sum(1 for _ in document.iterate_items())

    logging.getLogger().removeHandler(handler)
    return {
        "status": "ok",
        "source_file": str(source),
        "source_sha256": sha256_file(source),
        "backend_page_count": page_count,
        "seconds": round(elapsed, 3),
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "counts": {
            "doc_pages": len(raw.get("pages", {}) or {}),
            "texts": len(raw.get("texts", []) or []),
            "tables": len(raw.get("tables", []) or []),
            "pictures": len(raw.get("pictures", []) or []),
            "groups": len(raw.get("groups", []) or []),
            "items_total": total_items,
            "items_with_provenance": items_with_prov,
        },
        "raw_json_sha256": sha256_bytes((out_dir / "docling_raw.json").read_bytes()),
        "markdown_sha256": sha256_bytes((out_dir / "docling.md").read_bytes()),
        "assets": assets,
        "converter_warnings": warnings,
    }


def probe_file(source: Path, out_dir: Path, *, python: str) -> dict:
    """Convert one file in a child process, retrying once, and record what happened."""
    source, out_dir = Path(source), Path(out_dir)
    attempts = []
    for attempt in range(2):
        started = utc_now()
        command = [python, "-m", "scripts.odp_m0.docling_probe", "--worker", "--file", str(source), "--out", str(out_dir)]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, env=env)
        except subprocess.TimeoutExpired:
            attempts.append({"started_at": started, "status": "timeout", "seconds": PROBE_TIMEOUT_SECONDS})
            _discard(out_dir)
            continue
        if result.returncode != 0:
            attempts.append({"started_at": started, "status": "error", "stderr_tail": result.stderr[-1500:]})
            _discard(out_dir)
            continue
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        payload["attempts"] = attempts + [{"started_at": started, "status": "ok"}]
        return payload
    return {
        "status": "failed",
        "source_file": str(source),
        "source_sha256": sha256_file(source),
        "reason": "conversion failed twice",
        "attempts": attempts,
    }


def _discard(out_dir: Path) -> None:
    """A failed attempt must not leave an artifact that looks successful."""
    for name in ("docling_raw.json", "docling.md"):
        target = out_dir / name
        if target.exists():
            target.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docling native ODP probe")
    parser.add_argument("--worker", action="store_true", help="internal: run one conversion and print JSON")
    parser.add_argument("--file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not args.worker:
        print(json.dumps(probe_file(Path(args.file), Path(args.out), python=sys.executable), ensure_ascii=False))
        return 0
    payload = _convert_in_child(Path(args.file), Path(args.out))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
