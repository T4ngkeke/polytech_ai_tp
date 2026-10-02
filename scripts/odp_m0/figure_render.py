"""Render one figure object on its own, so the picture is the drawing and nothing else.

Cropping a figure out of a rendered page has two failure modes: the drawing can
spill past its own frame and get cut, and the crop can swallow neighbouring body
text. Both were seen on this courseware. Rendering a copy of the slide that
holds only the target object avoids both: the drawing is whole, nothing else is
in the frame, and the white margin can be trimmed away safely.

The original file is never modified. The copy lives in the experiment directory.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import NS, M0Error, sha256_file
from .inventory import Q
from .render import render_file

KEEP_TAGS = {Q["office"] + "forms"}

# A drawing page with nothing on it but the object: no master background, no
# header, footer, date or page number. Without this the master's own artwork
# fills the page and the drawing cannot be told apart from the decoration.
BLANK_PAGE_STYLE = "odp-m0-blank-page"
BLANK_STYLE_XML = (
    f'<style:style xmlns:style="{NS["style"]}" xmlns:draw="{NS["draw"]}" '
    f'xmlns:presentation="{NS["presentation"]}" style:name="{BLANK_PAGE_STYLE}" '
    'style:family="drawing-page">'
    '<style:drawing-page-properties draw:fill="solid" draw:fill-color="#ffffff" '
    'presentation:background-visible="false" presentation:background-objects-visible="false" '
    'presentation:display-header="false" presentation:display-footer="false" '
    'presentation:display-page-number="false" presentation:display-date-time="false"/>'
    "</style:style>"
)


def _object_index(object_path: str) -> tuple[str, int]:
    """The last step of our object path, for example draw:frame[3]."""
    tail = object_path.rsplit("/", 1)[-1]
    name, _, index = tail.partition("[")
    return name, int(index.rstrip("]") or 1)


def write_single_object_copy(odp: Path, slide_no: int, object_path: str, target: Path) -> dict:
    """A copy of the presentation holding one slide with one object on it."""
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    with zipfile.ZipFile(odp) as source:
        content = ET.fromstring(source.read("content.xml"))
        presentation = next(iter(content.iter(Q["office"] + "presentation")), None)
        if presentation is None:
            raise M0Error(f"{odp.name}: no office:presentation element")
        pages = presentation.findall(Q["draw"] + "page")
        if slide_no > len(pages):
            raise M0Error(f"{odp.name}: no slide {slide_no}")
        keep = pages[slide_no - 1]
        for page in pages:
            if page is not keep:
                presentation.remove(page)

        wanted_name, wanted_index = _object_index(object_path)
        counts: dict[str, int] = {}
        removed = 0
        for child in list(keep):
            tag = child.tag
            prefixed = tag
            for prefix, uri in NS.items():
                if tag.startswith("{" + uri + "}"):
                    prefixed = f"{prefix}:{tag.split('}')[1]}"
                    break
            if prefixed == "presentation:notes":
                keep.remove(child)
                continue
            if tag in KEEP_TAGS:
                continue
            counts[prefixed] = counts.get(prefixed, 0) + 1
            if not (prefixed == wanted_name and counts[prefixed] == wanted_index):
                keep.remove(child)
                removed += 1

        if not len(keep):
            raise M0Error(f"{odp.name}: slide {slide_no} has no {object_path} to keep")

        automatic = next(iter(content.iter(Q["office"] + "automatic-styles")), None)
        if automatic is None:
            automatic = ET.SubElement(content, Q["office"] + "automatic-styles")
            content.remove(automatic)
            content.insert(0, automatic)
        automatic.append(ET.fromstring(BLANK_STYLE_XML))
        keep.set(Q["draw"] + "style-name", BLANK_PAGE_STYLE)

        data = ET.tostring(content, encoding="UTF-8", xml_declaration=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
            for info in source.infolist():
                out.writestr(info.filename, data if info.filename == "content.xml" else source.read(info.filename))
    return {"path": str(target), "sha256": sha256_file(target), "removed_siblings": removed}


def _trim(image, tolerance: int = 18):
    """Cut the flat border away, and say whether the drawing reached the page edge."""
    from PIL import Image, ImageChops

    rgb = image.convert("RGB")
    background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    difference = ImageChops.difference(rgb, background).convert("L").point(lambda v: 255 if v > tolerance else 0)
    box = difference.getbbox()
    if box is None:
        return None, ["empty"]
    width, height = rgb.size
    at_edge = []
    if box[0] <= 0:
        at_edge.append("left")
    if box[1] <= 0:
        at_edge.append("top")
    if box[2] >= width:
        at_edge.append("right")
    if box[3] >= height:
        at_edge.append("bottom")
    pad = 6
    padded = (max(0, box[0] - pad), max(0, box[1] - pad),
              min(width, box[2] + pad), min(height, box[3] + pad))
    return rgb.crop(padded), at_edge


def render_object(odp: Path, slide_no: int, object_path: str, work_dir: Path, target: Path,
                   *, dpi: int = 150) -> dict:
    """Render just this object and trim it. Returns the picture and its quality."""
    import subprocess

    from PIL import Image

    work_dir.mkdir(parents=True, exist_ok=True)
    copy_info = write_single_object_copy(odp, slide_no, object_path, work_dir / f"single-{slide_no}.odp")
    result = render_file(Path(copy_info["path"]), work_dir / "render", hidden_slides=[])
    if result.status != "ok" or not result.pdf_path:
        return {"status": "failed", "reason": result.reason or "the single object copy did not render",
                "copy": copy_info}

    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", "1", "-l", "1",
                    result.pdf_path, str(work_dir / "page")], capture_output=True, timeout=120)
    rendered = next(iter(sorted(work_dir.glob("page-*.png"))), None)
    if rendered is None:
        return {"status": "failed", "reason": "the render produced no image", "copy": copy_info}

    with Image.open(rendered) as image:
        trimmed, at_edge = _trim(image)
    if trimmed is None:
        return {"status": "failed", "reason": "the object rendered as an empty area", "copy": copy_info}

    target.parent.mkdir(parents=True, exist_ok=True)
    trimmed.save(target)
    return {
        "status": "ok" if not at_edge else "clipped",
        "path": str(target),
        "sha256": sha256_file(target),
        "size_px": trimmed.size,
        "reached_page_edge": at_edge,
        "reason": None if not at_edge else f"the drawing reaches the page edge on: {', '.join(at_edge)}",
        "source_object_path": object_path,
        "method": "single object rendered on its own slide, then trimmed",
        "copy": copy_info,
        "note": "our own render, not a teacher export",
    }
