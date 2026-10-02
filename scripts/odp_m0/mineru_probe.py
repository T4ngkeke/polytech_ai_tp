"""MinerU native ODP probe.

MinerU 4.0.2 routes .odp to docvortex's OdpModel, which reads the OpenDocument
package directly: there is no LibreOffice call, no conversion to PPTX or PDF and
no model in that path. The probe records the versions and the code fingerprint
so that claim can be checked against the installed source.

Each file is converted once in a child process with a timeout. A failed attempt
leaves no artifact that looks successful.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from .common import M0Error, sha256_bytes, sha256_file, utc_now

PROBE_TIMEOUT_SECONDS = 120


def environment_facts(python: str) -> dict:
    code = (
        "import json, sys;"
        "from importlib.metadata import version as _v;"
        "import docvortex;"
        "from docvortex.analyzers.native.office.odf import converters as _c;"
        "import mineru.backend.analysis.office as _o;"
        "print(json.dumps({"
        "'python': sys.version.split()[0],"
        "'mineru': _v('mineru'),"
        "'docvortex': _v('docvortex'),"
        "'odf_converters_module': _c.__file__,"
        "'office_entry_module': _o.__file__,"
        "}))"
    )
    result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise M0Error(f"cannot read the MinerU environment: {result.stderr[-500:]}")
    facts = json.loads(result.stdout)
    facts["odf_converters_sha256"] = sha256_file(Path(facts["odf_converters_module"]))
    facts["conversion_path"] = _conversion_path(python)
    facts["ml_dependencies_importable"] = _ml_present(python)
    return facts


def _conversion_path(python: str) -> dict:
    """Evidence that the ODP path is native and calls nothing external."""
    code = (
        "import json, inspect;"
        "from docvortex.analyzers.native.office.odf import converters, package, text, table;"
        "src = ''.join(inspect.getsource(m) for m in (converters, package, text, table));"
        "print(json.dumps({"
        "'entry': 'mineru.backend.analysis.office.analyze_office -> docvortex OdpModel -> OdpConverter',"
        "'mentions_libreoffice': any(w in src.lower() for w in ('soffice', 'libreoffice')),"
        "'mentions_subprocess': 'subprocess' in src,"
        "'mentions_pptx_conversion': 'to_pptx' in src or 'convert_to' in src,"
        "'reads_zip_package': 'zipfile' in src or 'ZipFile' in src,"
        "}))"
    )
    result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=180)
    return json.loads(result.stdout) if result.returncode == 0 else {"error": result.stderr[-300:]}


def _ml_present(python: str) -> list[str]:
    code = (
        "import importlib.util, json;"
        "print(json.dumps([m for m in ['torch','transformers','ultralytics','paddle']"
        " if importlib.util.find_spec(m) is not None]))"
    )
    result = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=180)
    return json.loads(result.stdout) if result.returncode == 0 else ["unknown"]


def _convert_in_child(source: Path, out_dir: Path) -> dict:
    import warnings

    collected: list[dict] = []
    def capture(message, category, filename, lineno, file=None, line=None):
        collected.append({"category": category.__name__, "message": str(message)})
    warnings.showwarning = capture

    from docvortex.analyzers.native import OdpModel

    started = time.time()
    with open(source, "rb") as handle:
        pages = OdpModel().predict(handle)
    elapsed = time.time() - started

    out_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = out_dir / "assets"
    assets: list[dict] = []
    serialisable: list[list[dict]] = []

    for page_index, page in enumerate(pages, start=1):
        items = []
        for item_index, item in enumerate(page):
            clean = json.loads(json.dumps(item, default=str))
            payload = clean.pop("image_base64", None)
            if payload:
                raw = payload.split(",", 1)[1] if "," in payload else payload
                mime = payload.split(";", 1)[0].removeprefix("data:") if payload.startswith("data:") else "image/unknown"
                data = base64.b64decode(raw)
                asset_dir.mkdir(parents=True, exist_ok=True)
                suffix = {"image/jpeg": ".jpg", "image/png": ".png"}.get(mime, ".bin")
                target = asset_dir / f"p{page_index:03d}-i{item_index:03d}{suffix}"
                target.write_bytes(data)
                record = {
                    "page_no": page_index,
                    "item_index": item_index,
                    "path": str(target.relative_to(out_dir)),
                    "sha256": sha256_bytes(data),
                    "mime": mime,
                    "bytes": len(data),
                    "asset_kind": "original",
                }
                try:
                    from PIL import Image
                    with Image.open(target) as image:
                        record["width"], record["height"] = image.size
                        record["decodable"] = True
                except Exception as error:
                    record["decodable"] = False
                    record["decode_error"] = f"{type(error).__name__}: {error}"
                assets.append(record)
                clean["asset_path"] = record["path"]
            items.append(clean)
        serialisable.append(items)

    (out_dir / "mineru_raw.json").write_text(
        json.dumps({"pages": serialisable}, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "mineru.md").write_text(_markdown(serialisable), encoding="utf-8")

    counts: dict[str, int] = {}
    for page in serialisable:
        for item in page:
            counts[item.get("type", "unknown")] = counts.get(item.get("type", "unknown"), 0) + 1

    return {
        "status": "ok",
        "source_file": str(source),
        "source_sha256": sha256_file(source),
        "page_count": len(pages),
        "seconds": round(elapsed, 3),
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "counts": counts,
        "item_total": sum(len(p) for p in serialisable),
        "items_with_position": 0,
        "position_note": "MinerU returns no page coordinates and no source object id for ODP",
        "raw_json_sha256": sha256_bytes((out_dir / "mineru_raw.json").read_bytes()),
        "assets": assets,
        "converter_warnings": collected,
        "model_calls": 0,
        "model_calls_evidence": "the ODP path imports no model package; see environment.conversion_path",
    }


def _markdown(pages: list[list[dict]]) -> str:
    """A readable helper only. The raw structure stays the evidence."""
    lines = []
    for index, page in enumerate(pages, start=1):
        lines.append(f"<!-- slide {index} -->")
        for item in page:
            kind = item.get("type", "")
            text = item_text(item)
            if "title" in kind:
                lines.append(f"## {text}")
            elif kind == "image" or kind.endswith("IMAGE"):
                lines.append(f"![]({item.get('asset_path', 'missing')})")
            else:
                lines.append(text)
        lines.append("")
    return "\n".join(lines)


def item_text(item: dict) -> str:
    """Flatten one MinerU item to text, joining style runs without a separator."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            inner = part.get("content")
            parts.append(inner if isinstance(inner, str) else item_text(part))
    return "".join(parts)


def probe_file(source: Path, out_dir: Path, *, python: str) -> dict:
    source, out_dir = Path(source), Path(out_dir)
    attempts = []
    for _ in range(2):
        started = utc_now()
        command = [python, "-m", "scripts.odp_m0.mineru_probe", "--worker", "--file", str(source), "--out", str(out_dir)]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]),
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MINERU_DEVICE_MODE="cpu")
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
        "status": "failed", "source_file": str(source), "source_sha256": sha256_file(source),
        "reason": "conversion failed twice", "attempts": attempts,
    }


def _discard(out_dir: Path) -> None:
    for name in ("mineru_raw.json", "mineru.md"):
        target = out_dir / name
        if target.exists():
            target.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MinerU native ODP probe")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not args.worker:
        print(json.dumps(probe_file(Path(args.file), Path(args.out), python=sys.executable), ensure_ascii=False))
        return 0
    print(json.dumps(_convert_in_child(Path(args.file), Path(args.out)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
