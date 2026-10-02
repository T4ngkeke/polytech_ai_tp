"""Shared helpers: hashing, safe ZIP/XML reading, run directories, fingerprints.

Every function here is read-only with respect to the source courseware.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "cours" / "odp_files"
RESULT_ROOT = REPO_ROOT / "documentation" / "ingestion-agent-acceptance" / "m0-odp"
ARTIFACT_ROOT = Path("/tmp/odp-m0-artifacts")
SCHEMA_VERSION = "m0-odp-1"

# OpenDocument namespaces we read. Anything outside this map is reported as unknown.
NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xml": "http://www.w3.org/XML/1998/namespace",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)


class M0Error(RuntimeError):
    """A failure we want reported with a reason, never swallowed."""


@dataclass(frozen=True)
class ZipLimits:
    """Defaults from the execution plan. A hit is recorded, never treated as empty."""

    max_archive_bytes: int = 500 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_entry_bytes: int = 200 * 1024 * 1024
    max_entries: int = 20_000


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value) -> str:
    """Stable hash of a JSON-serialisable value; used to bind reviews to artifacts."""
    return sha256_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def write_json(path: Path, value, *, overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise M0Error(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    return path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def new_run_dir(run_id: str, root: Path | None = None) -> Path:
    """Run directories are never reused: an existing id is an error, not an overwrite."""
    base = (root or RESULT_ROOT) / "runs" / run_id
    if base.exists():
        raise M0Error(f"run_id already exists: {base}")
    base.mkdir(parents=True)
    return base


def parse_xml(data: bytes, *, where: str = "xml") -> ET.Element:
    """Parse without any doctype or entity handling.

    Rejecting the declaration outright is stricter than relying on the parser's
    defaults, and it keeps the package free of extra dependencies.
    """
    head = data[:4096].lstrip()
    lowered = head.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise M0Error(f"{where}: doctype or entity declaration is not accepted")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise M0Error(f"{where}: malformed XML: {exc}") from exc


@dataclass
class OdpArchive:
    """A read-only view of one ODP, with the limit checks already applied."""

    path: Path
    sha256: str
    size_bytes: int
    entries: list[dict]
    limit_hits: list[dict] = field(default_factory=list)
    _zip: zipfile.ZipFile | None = None

    def read(self, name: str) -> bytes:
        assert self._zip is not None
        return self._zip.read(name)

    def xml(self, name: str) -> ET.Element:
        return parse_xml(self.read(name), where=f"{self.path.name}:{name}")

    def has(self, name: str) -> bool:
        return any(entry["name"] == name for entry in self.entries)

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "OdpArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_odp(path: Path, limits: ZipLimits | None = None) -> OdpArchive:
    """Open an ODP for reading, refusing paths and sizes we do not accept."""
    limits = limits or ZipLimits()
    path = Path(path)
    size = path.stat().st_size
    if size > limits.max_archive_bytes:
        raise M0Error(f"{path.name}: archive is {size} bytes, over the {limits.max_archive_bytes} byte limit")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise M0Error(f"{path.name}: not a readable ZIP: {exc}") from exc

    entries: list[dict] = []
    limit_hits: list[dict] = []
    total = 0
    for info in archive.infolist():
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0][1:2]:
            archive.close()
            raise M0Error(f"{path.name}: entry escapes the archive root: {name!r}")
        if info.file_size > limits.max_entry_bytes:
            limit_hits.append({"entry": name, "reason": "entry_over_limit", "size_bytes": info.file_size})
            continue
        total += info.file_size
        if total > limits.max_total_bytes:
            archive.close()
            raise M0Error(f"{path.name}: expanded size passes {limits.max_total_bytes} bytes")
        if len(entries) >= limits.max_entries:
            archive.close()
            raise M0Error(f"{path.name}: more than {limits.max_entries} entries")
        entries.append({"name": name, "size_bytes": info.file_size, "compressed_bytes": info.compress_size})

    return OdpArchive(
        path=path,
        sha256=sha256_file(path),
        size_bytes=size,
        entries=entries,
        limit_hits=limit_hits,
        _zip=archive,
    )


def package_fingerprint() -> dict:
    """Hash of this package's own code, so a run says which code produced it."""
    here = Path(__file__).resolve().parent
    files = {}
    for source in sorted(here.glob("*.py")):
        files[source.name] = sha256_file(source)
    combined = sha256_json(files)
    return {"files": files, "combined": combined}


def git_snapshot() -> dict:
    """Branch and head only. No environment variables, no keys."""
    def run(args: list[str]) -> str:
        try:
            return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "head": run(["git", "rev-parse", "HEAD"]),
        "dirty_paths": len([line for line in run(["git", "status", "--porcelain"]).splitlines() if line]),
    }


def tool_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {type(exc).__name__}"
    return (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else "unknown"


def object_path(page_index: int, trail: list[tuple[str, int]]) -> str:
    """Source identity: the XML object path inside the slide, 1-based everywhere.

    It is stable for repeated parses of the same file. It says nothing about
    stability across file versions and must not be presented as if it did.
    """
    parts = [f"/office:document-content/office:body/office:presentation/draw:page[{page_index}]"]
    for tag, index in trail:
        parts.append(f"{tag}[{index}]")
    return "/".join(parts) if len(parts) == 1 else parts[0] + "/" + "/".join(parts[1:])


def local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def prefixed_name(tag: str) -> str:
    """draw:frame style name, or the raw tag when the namespace is unknown to us."""
    if "}" not in tag:
        return tag
    uri, name = tag[1:].split("}", 1)
    for prefix, known in NS.items():
        if known == uri:
            return f"{prefix}:{name}"
    return f"{{{uri}}}{name}"
