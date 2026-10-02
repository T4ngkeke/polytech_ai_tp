"""A2: render previews with an isolated LibreOffice and verify the page mapping.

The host blocks unprivileged user namespaces, so unshare and bwrap cannot sandbox
soffice here. Rendering therefore runs inside a container built from
Dockerfile.render with --network none: the process has no network at all, macros
are disabled in the seeded profile, and the source file is mounted read-only.

The preview we produce is our own render, not a teacher export. It is never
presented as independent ground truth.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import NS, M0Error, sha256_file, utc_now
from .inventory import Q, _resolve_visibility, _collect_styles

RENDER_IMAGE = "edullm-odp-m0-render:1"
CONVERT_TIMEOUT_SECONDS = 120

# Macro execution off, no link updates, no document recovery. The container has
# no network either; this is the second layer, not the only one.
PROFILE_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Filter/Microsoft/Import">
  <prop oor:name="MathTypeToMath" oor:op="fuse"><value>true</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Save/Document">
  <prop oor:name="CreateBackup" oor:op="fuse"><value>false</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Recovery/AutoSave">
  <prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop>
 </item>
</oor:items>
"""


@dataclass
class RenderResult:
    file_name: str
    status: str
    pdf_path: str | None
    pdf_sha256: str | None
    pdf_page_count: int | None
    seconds: float
    attempts: list[dict]
    cleaned_copy: dict | None
    reason: str | None = None

    def dump(self) -> dict:
        return {
            "file_name": self.file_name,
            "status": self.status,
            "pdf_path": self.pdf_path,
            "pdf_sha256": self.pdf_sha256,
            "pdf_page_count": self.pdf_page_count,
            "seconds": round(self.seconds, 2),
            "attempts": self.attempts,
            "cleaned_copy": self.cleaned_copy,
            "reason": self.reason,
        }


def isolation_report() -> dict:
    """What the isolation actually rests on, stated plainly for the report."""
    image_id = subprocess.run(
        ["docker", "images", "--no-trunc", "--format", "{{.ID}}", RENDER_IMAGE],
        capture_output=True, text=True,
    ).stdout.strip()
    return {
        "method": "docker container, --network none",
        "image": RENDER_IMAGE,
        "image_id": image_id or "missing",
        "network": "none (no interfaces beyond loopback inside the container)",
        "macros": "MacroSecurityLevel=3 and DisableMacrosExecution=true in a fresh profile",
        "profile": "fresh UserInstallation per run; the user profile is never reused",
        "source_mount": "read-only",
        "host_sandbox_note": "unprivileged user namespaces are disabled on this host, so unshare and bwrap are unavailable",
    }


def _hidden_slide_numbers(odp: Path) -> list[int]:
    with zipfile.ZipFile(odp) as archive:
        content = ET.fromstring(archive.read("content.xml"))
        styles_root = ET.fromstring(archive.read("styles.xml")) if "styles.xml" in archive.namelist() else ET.Element("e")
    styles = _collect_styles(content)
    styles.update({k: v for k, v in _collect_styles(styles_root).items() if k not in styles})
    hidden = []
    for index, page in enumerate(content.iter(Q["draw"] + "page"), start=1):
        visibility, _ = _resolve_visibility(page.attrib.get(Q["draw"] + "style-name"), styles)
        if visibility == "hidden":
            hidden.append(index)
    return hidden


def make_visible_only_copy(odp: Path, target: Path) -> dict:
    """Write a copy with hidden slides and notes removed. The original is untouched."""
    hidden = _hidden_slide_numbers(odp)
    removed_notes = 0
    with zipfile.ZipFile(odp) as source:
        content = ET.fromstring(source.read("content.xml"))
        presentation = None
        for element in content.iter(Q["office"] + "presentation"):
            presentation = element
            break
        if presentation is None:
            raise M0Error(f"{odp.name}: no office:presentation element")
        for index, page in enumerate(list(presentation.findall(Q["draw"] + "page")), start=1):
            if index in hidden:
                presentation.remove(page)
                continue
            for notes in list(page.findall(Q["presentation"] + "notes")):
                page.remove(notes)
                removed_notes += 1
        for prefix, uri in NS.items():
            ET.register_namespace(prefix, uri)
        new_content = ET.tostring(content, encoding="UTF-8", xml_declaration=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            for info in source.infolist():
                data = new_content if info.filename == "content.xml" else source.read(info.filename)
                out.writestr(info.filename, data)
    return {
        "path": str(target),
        "removed_hidden_slides": hidden,
        "removed_notes_nodes": removed_notes,
        "sha256": sha256_file(target),
    }


def _run_soffice(work_dir: Path, source_name: str, log_path: Path) -> dict:
    """One conversion attempt in its own container. Only that container is killed."""
    container = f"odp-m0-render-{uuid.uuid4().hex[:12]}"
    command = [
        "docker", "run", "--rm", "--name", container,
        "--network", "none",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{work_dir}:/work",
        "-w", "/work",
        RENDER_IMAGE,
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
        "-env:UserInstallation=file:///work/profile",
        "--convert-to", "pdf:impress_pdf_Export",
        "--outdir", "/work/out", f"/work/in/{source_name}",
    ]
    started = utc_now()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_SECONDS)
        record = {
            "started_at": started,
            "return_code": result.returncode,
            "timed_out": False,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "kill", container], capture_output=True)
        record = {
            "started_at": started,
            "return_code": None,
            "timed_out": True,
            "stdout_tail": (exc.stdout or b"").decode("utf-8", "replace")[-2000:] if isinstance(exc.stdout, bytes) else "",
            "stderr_tail": f"timeout after {CONVERT_TIMEOUT_SECONDS}s",
        }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"--- attempt {started} ---\nrc={record['return_code']} timeout={record['timed_out']}\n")
        handle.write(record["stdout_tail"] + "\n" + record["stderr_tail"] + "\n")
    return record


def pdf_page_count(pdf: Path) -> int | None:
    result = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60)
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.M)
    return int(match.group(1)) if match else None


def pdf_page_text(pdf: Path, page_no: int) -> str:
    result = subprocess.run(
        # -raw keeps a text box's paragraphs together. -layout interleaves them
        # with whatever is drawn beside them, which breaks anchor comparison.
        ["pdftotext", "-f", str(page_no), "-l", str(page_no), "-raw", str(pdf), "-"],
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout


def render_file(odp: Path, out_dir: Path, *, hidden_slides: list[int] | None = None) -> RenderResult:
    """Render one ODP to PDF. Hidden slides, if any, are removed from a copy first."""
    import time

    odp = Path(odp)
    work = out_dir / "work"
    (work / "in").mkdir(parents=True, exist_ok=True)
    (work / "out").mkdir(parents=True, exist_ok=True)
    profile = work / "profile" / "user"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "registrymodifications.xcu").write_text(PROFILE_XCU, encoding="utf-8")

    hidden = hidden_slides if hidden_slides is not None else _hidden_slide_numbers(odp)
    cleaned = None
    if hidden:
        cleaned = make_visible_only_copy(odp, work / "in" / odp.name)
    else:
        shutil.copy2(odp, work / "in" / odp.name)

    started = time.time()
    attempts = [_run_soffice(work, odp.name, out_dir / "render.log")]
    produced = work / "out" / (odp.stem + ".pdf")
    if not produced.exists():
        attempts.append(_run_soffice(work, odp.name, out_dir / "render.log"))
    seconds = time.time() - started

    if not produced.exists():
        return RenderResult(odp.name, "failed", None, None, None, seconds, attempts, cleaned,
                            reason="soffice produced no PDF after two attempts")

    final = out_dir / (odp.stem + ".pdf")
    shutil.move(str(produced), final)
    return RenderResult(
        odp.name, "ok", str(final), sha256_file(final), pdf_page_count(final), seconds, attempts, cleaned
    )


def _anchor(text: str) -> str:
    """Normalise for comparison: the PDF and the XML differ in quotes and spaces."""
    text = text.replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u0153", "oe").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_mapping(inventory_file: dict, result: RenderResult) -> dict:
    """Build source_slide_no -> visible_page_no -> preview_pdf_page_no and check it.

    Equal page counts prove nothing on their own, so each page is checked against
    a text anchor from the source. Pages without usable text, or whose anchor is
    not unique, are listed for visual review instead of being called verified.
    """
    pages = inventory_file["pages"]
    visible = [p for p in pages if p["visibility"] == "visible"]
    mapping = []
    visible_no = 0
    for page in pages:
        if page["visibility"] != "visible":
            mapping.append({
                "source_slide_no": page["source_slide_no"],
                "visible_page_no": None,
                "preview_pdf_page_no": None,
                "check": "hidden_slide_excluded",
            })
            continue
        visible_no += 1
        mapping.append({
            "source_slide_no": page["source_slide_no"],
            "visible_page_no": visible_no,
            "preview_pdf_page_no": visible_no if result.status == "ok" else None,
            "check": "pending",
        })

    issues = []
    if result.status != "ok":
        for entry in mapping:
            entry["check"] = "render_failed"
        return {"mapping": mapping, "verified_pages": 0, "needs_visual_review": [], "issues": [
            {"code": "render_failure", "severity": "blocking", "message": result.reason or "render failed"}
        ]}

    if result.pdf_page_count != len(visible):
        issues.append({
            "code": "preview_mapping_failed",
            "severity": "blocking",
            "message": f"PDF has {result.pdf_page_count} pages, {len(visible)} slides are visible",
        })

    pdf = Path(result.pdf_path)
    page_texts = {}
    for page_no in range(1, (result.pdf_page_count or 0) + 1):
        page_texts[page_no] = _anchor(pdf_page_text(pdf, page_no))

    needs_visual = []
    verified = 0
    for entry in mapping:
        if entry["visible_page_no"] is None:
            continue
        source_page = pages[entry["source_slide_no"] - 1]
        # One paragraph at a time. A whole shape's text is not contiguous in the
        # PDF, because the renderer puts list markers between the paragraphs.
        anchors = [
            _anchor(line)
            for obj in source_page["objects"]
            if obj["role"] == "shape"
            for line in obj["text_sample"].splitlines()
            if len(_anchor(line)) >= 12
        ]
        target = entry["preview_pdf_page_no"]
        if not anchors or target not in page_texts:
            entry["check"] = "needs_visual_review"
            entry["reason"] = "no text anchor of 12 characters or more on this slide" if not anchors else "no such preview page"
            needs_visual.append(entry["source_slide_no"])
            continue
        anchor = max(anchors, key=len)
        matches = [page_no for page_no, text in page_texts.items() if anchor[:80] in text]
        if not matches:
            # The anchor may be split by the renderer. Fall back to word overlap,
            # which is weaker evidence and is recorded under its own name.
            best, scores = _word_overlap(anchor, page_texts)
            if best == target and scores[target] >= 0.8:
                entry["check"] = "word_overlap_unique_match"
                entry["overlap"] = round(scores[target], 2)
                verified += 1
                continue
            matches = [page for page, score in scores.items() if score >= 0.8]
        if matches == [target]:
            entry["check"] = "anchor_unique_match"
            verified += 1
        elif target in matches:
            entry["check"] = "anchor_matches_but_not_unique"
            entry["reason"] = f"anchor also found on pages {[m for m in matches if m != target]}"
            needs_visual.append(entry["source_slide_no"])
        else:
            entry["check"] = "anchor_not_found"
            entry["reason"] = f"anchor found on pages {matches}"
            needs_visual.append(entry["source_slide_no"])
            issues.append({
                "code": "preview_mapping_failed",
                "severity": "blocking",
                "source_slide_no": entry["source_slide_no"],
                "message": f"slide {entry['source_slide_no']} anchor does not appear on preview page {target}",
            })
    return {
        "mapping": mapping,
        "verified_pages": verified,
        "needs_visual_review": needs_visual,
        "issues": issues,
    }


def _word_overlap(anchor: str, page_texts: dict) -> tuple[int | None, dict]:
    """Share of the anchor's words present on each page, and the single best page."""
    words = [w for w in re.split(r"\W+", anchor) if len(w) > 2]
    scores = {}
    for page_no, text in page_texts.items():
        page_words = set(re.split(r"\W+", text))
        scores[page_no] = (sum(1 for w in words if w in page_words) / len(words)) if words else 0.0
    if not scores:
        return None, scores
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, scores
    return ranked[0][0], scores


def page_images(pdf: Path, out_dir: Path, *, dpi: int = 110) -> list[dict]:
    """Whole-page PNGs from our own preview PDF, used for the comparison pages."""
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "page"
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(prefix)], capture_output=True, timeout=600)
    images = []
    for image in sorted(out_dir.glob("page-*.png")):
        page_no = int(re.search(r"page-(\d+)\.png", image.name).group(1))
        images.append({"preview_pdf_page_no": page_no, "path": str(image), "sha256": sha256_file(image), "kind": "rendered_page"})
    return images
