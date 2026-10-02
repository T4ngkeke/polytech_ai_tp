"""A1: read-only inventory of the source ODP files.

Reads the ZIP manifest, content.xml and styles.xml, resolves slide visibility
through the drawing-page style chain, and lists every object it finds. Objects
it cannot classify are listed as ``unknown`` rather than dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import (
    NS,
    M0Error,
    OdpArchive,
    ZipLimits,
    local_name,
    object_path,
    open_odp,
    prefixed_name,
    sha256_bytes,
    utc_now,
)

Q = {prefix: "{%s}" % uri for prefix, uri in NS.items()}

# Tags we classify. Everything else on a slide becomes an unknown object.
SHAPE_TAGS = {
    "draw:frame": "frame",
    "draw:custom-shape": "custom_shape",
    "draw:g": "group_shape",
    "draw:line": "connector",
    "draw:connector": "connector",
    "draw:polyline": "connector",
    "draw:path": "shape_path",
    "draw:rect": "shape_rect",
    "draw:ellipse": "shape_ellipse",
    "draw:circle": "shape_ellipse",
    "draw:caption": "caption",
    "draw:measure": "measure",
    "draw:page-thumbnail": "page_thumbnail",
    "draw:control": "control",
}
# Parts of a shape rather than shapes of their own. They are recorded, with the
# role "component", so an image href keeps its own path without inflating the
# shape counts or being reported as something we failed to classify.
COMPONENT_TAGS = {
    "draw:text-box": "text_body",
    "draw:image": "image_ref",
    "draw:object": "object_ref",
    "draw:object-ole": "object_ole_ref",
    "draw:enhanced-geometry": "shape_geometry",
    "draw:glue-point": "glue_point",
    "draw:handle": "shape_handle",
    "draw:contour-polygon": "contour",
    "draw:contour-path": "contour",
    "text:p": "paragraph",
    "text:h": "heading_paragraph",
    "text:list": "list",
    "svg:title": "shape_title",
    "svg:desc": "shape_description",
    "office:event-listeners": "event_listeners",
    "table:table": "table_body",
}
# Present on every slide and not content: skipped without being called unknown.
IGNORED_TAGS = {"office:forms", "presentation:notes", "anim:par", "anim:seq", "draw:layer-set"}

FORMULA_MIME = "application/vnd.oasis.opendocument.formula"
IMAGE_MIME_PREFIX = "image/"


@dataclass
class ObjectRecord:
    object_path: str
    tag: str
    kind: str
    role: str = "shape"
    xml_id: str | None = None
    draw_name: str | None = None
    text_sample: str = ""
    text_sha256: str | None = None
    href: str | None = None
    href_media_type: str | None = None
    presentation_class: str | None = None
    position_cm: dict | None = None
    child_object_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def dump(self) -> dict:
        return {
            "object_path": self.object_path,
            "tag": self.tag,
            "kind": self.kind,
            "role": self.role,
            "xml_id": self.xml_id,
            "draw_name": self.draw_name,
            "text_sample": self.text_sample,
            "text_sha256": self.text_sha256,
            "href": self.href,
            "href_media_type": self.href_media_type,
            "presentation_class": self.presentation_class,
            "position_cm": self.position_cm,
            "child_object_paths": self.child_object_paths,
            "notes": self.notes,
        }


def _attr(element: ET.Element, prefix: str, name: str) -> str | None:
    return element.attrib.get(Q[prefix] + name)


def _length_cm(raw: str | None) -> float | None:
    if not raw:
        return None
    match = re.fullmatch(r"(-?[\d.]+)(cm|mm|in|pt)", raw.strip())
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2)
    return round({"cm": value, "mm": value / 10, "in": value * 2.54, "pt": value * 2.54 / 72}[unit], 4)


def _text_of(element: ET.Element) -> str:
    """Slide text with the explicit ODF whitespace nodes decoded.

    text:s carries a repeat count, text:tab is a tab and text:line-break a newline.
    Reading itertext() alone silently loses all three.
    """
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            tag = prefixed_name(child.tag)
            if tag == "text:s":
                out.append(" " * int(_attr(child, "text", "c") or 1))
            elif tag == "text:tab":
                out.append("\t")
            elif tag == "text:line-break":
                out.append("\n")
            elif tag == "presentation:notes":
                continue
            else:
                if child.text:
                    out.append(child.text)
                walk(child)
            if child.tail:
                out.append(child.tail)

    if element.text:
        out.append(element.text)
    walk(element)
    return "".join(out)


def _paragraph_text(element: ET.Element) -> str:
    """Join the paragraphs of a shape, keeping paragraph breaks as newlines."""
    paragraphs = element.findall(f".//{Q['text']}p") + element.findall(f".//{Q['text']}h")
    if not paragraphs:
        return _text_of(element)
    seen: list[str] = []
    for paragraph in element.iter():
        if prefixed_name(paragraph.tag) in {"text:p", "text:h"}:
            seen.append(_text_of(paragraph))
    return "\n".join(seen)


def _collect_styles(root: ET.Element) -> dict:
    """drawing-page styles, by name, with their parent and visibility setting."""
    styles: dict[str, dict] = {}
    for style in root.iter(Q["style"] + "style"):
        if _attr(style, "style", "family") != "drawing-page":
            continue
        name = _attr(style, "style", "name")
        if not name:
            continue
        visibility = None
        for props in style.iter(Q["style"] + "drawing-page-properties"):
            visibility = _attr(props, "presentation", "visibility") or visibility
        styles[name] = {"parent": _attr(style, "style", "parent-style-name"), "visibility": visibility}
    return styles


def _resolve_visibility(style_name: str | None, styles: dict) -> tuple[str, str]:
    """Follow the style chain. Returns (visibility, the basis for saying so)."""
    if not style_name:
        return "visible", "no drawing-page style on the slide; ODF default is visible"
    seen: list[str] = []
    name: str | None = style_name
    while name and name in styles and name not in seen:
        seen.append(name)
        entry = styles[name]
        if entry["visibility"]:
            return (
                "hidden" if entry["visibility"] == "hidden" else "visible",
                f"style chain {' -> '.join(seen)} sets presentation:visibility={entry['visibility']}",
            )
        name = entry["parent"]
    chain = " -> ".join(seen) if seen else style_name
    return "visible", f"style chain {chain} sets no presentation:visibility; ODF default is visible"


def _manifest_types(archive: OdpArchive) -> dict:
    if not archive.has("META-INF/manifest.xml"):
        return {}
    root = archive.xml("META-INF/manifest.xml")
    out = {}
    for entry in root.iter(Q["manifest"] + "file-entry"):
        path = _attr(entry, "manifest", "full-path")
        media = _attr(entry, "manifest", "media-type")
        if path:
            out[path.rstrip("/")] = media or ""
    return out


def _classify_object(element: ET.Element, manifest: dict) -> tuple[str, dict]:
    """Return a kind plus the extra fields that kind needs."""
    tag = prefixed_name(element.tag)
    extra: dict = {}
    if tag in COMPONENT_TAGS:
        kind = COMPONENT_TAGS[tag]
        href = _attr(element, "xlink", "href")
        if href:
            extra["href"] = href
            extra["href_media_type"] = manifest.get(href.lstrip("./").rstrip("/"), "") or None
        return kind, extra
    kind = SHAPE_TAGS.get(tag, "unknown")

    if tag == "draw:frame":
        child_tags = [prefixed_name(child.tag) for child in element]
        if any(t == "draw:image" for t in child_tags):
            kind = "embedded_image"
        elif any(t == "draw:object" for t in child_tags):
            kind = "embedded_object"
        elif any(t == "draw:object-ole" for t in child_tags):
            kind = "embedded_object_ole"
        elif any(t == "draw:text-box" for t in child_tags):
            kind = "text_box"
        elif any(t == "draw:plugin" for t in child_tags):
            kind = "plugin"
        elif any(t == "table:table" for t in child_tags):
            kind = "table"
        else:
            kind = "frame_other"
        if element.find(f".//{Q['table']}table") is not None:
            kind = "table"
        href = None
        for child in element:
            candidate = _attr(child, "xlink", "href")
            if candidate:
                href = candidate
                break
        if href:
            extra["href"] = href
            lookup = href.lstrip("./").rstrip("/")
            media = manifest.get(lookup, "")
            extra["href_media_type"] = media or None
            if media == FORMULA_MIME:
                kind = "embedded_formula"
            elif media.startswith("image/") and kind == "embedded_object":
                kind = "embedded_image"
            elif media.startswith("application/vnd.oasis.opendocument.spreadsheet"):
                kind = "embedded_spreadsheet"
            elif media.startswith("application/vnd.oasis.opendocument.graphics"):
                kind = "embedded_graphics"
    return kind, extra


def _walk_shapes(page: ET.Element, page_index: int, manifest: dict) -> list[ObjectRecord]:
    """Walk the slide's shapes, depth first, recording a path for each one."""
    records: list[ObjectRecord] = []

    def walk(node: ET.Element, trail: list[tuple[str, int]]) -> list[str]:
        counts: dict[str, int] = {}
        produced: list[str] = []
        for child in node:
            tag = prefixed_name(child.tag)
            if tag in IGNORED_TAGS:
                continue
            counts[tag] = counts.get(tag, 0) + 1
            child_trail = trail + [(tag, counts[tag])]
            path = object_path(page_index, child_trail)
            kind, extra = _classify_object(child, manifest)
            role = "component" if tag in COMPONENT_TAGS else "shape"
            text = _paragraph_text(child) if kind not in {"page_thumbnail"} else ""
            record = ObjectRecord(
                object_path=path,
                tag=tag,
                kind=kind,
                role=role,
                xml_id=_attr(child, "xml", "id") or child.attrib.get("id"),
                draw_name=_attr(child, "draw", "name"),
                text_sample=text,
                text_sha256=sha256_bytes(text.encode("utf-8")) if text.strip() else None,
                href=extra.get("href"),
                href_media_type=extra.get("href_media_type"),
                presentation_class=_attr(child, "presentation", "class"),
                position_cm={
                    "x": _length_cm(_attr(child, "svg", "x")),
                    "y": _length_cm(_attr(child, "svg", "y")),
                    "width": _length_cm(_attr(child, "svg", "width")),
                    "height": _length_cm(_attr(child, "svg", "height")),
                },
            )
            if kind == "unknown" and role == "shape":
                record.notes.append(f"unclassified tag {tag}")
            records.append(record)
            produced.append(path)
            if tag in {"draw:g", "draw:frame", "draw:custom-shape"}:
                record.child_object_paths = walk(child, child_trail)
        return produced

    walk(page, [])
    return records


def _fonts(root: ET.Element) -> list[str]:
    names = set()
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.endswith("}font-name") or key.endswith("}font-family"):
                names.add(value.strip("'\""))
    return sorted(n for n in names if n)


def _external_refs(root: ET.Element) -> list[str]:
    refs = set()
    for element in root.iter():
        href = element.attrib.get(Q["xlink"] + "href")
        if href and re.match(r"^(https?|ftp|file)://", href):
            refs.add(href)
    return sorted(refs)


def inspect_file(path: Path, limits: ZipLimits | None = None) -> dict:
    """Inventory one ODP. Any failure is returned as a reason, not an empty result."""
    path = Path(path)
    started = utc_now()
    try:
        archive = open_odp(path, limits)
    except M0Error as exc:
        return {"file_name": path.name, "status": "failed", "reason": str(exc), "inspected_at": started}

    with archive:
        try:
            content = archive.xml("content.xml")
            styles_root = archive.xml("styles.xml") if archive.has("styles.xml") else ET.Element("empty")
            manifest = _manifest_types(archive)
        except M0Error as exc:
            return {"file_name": path.name, "status": "failed", "reason": str(exc), "inspected_at": started}

        styles = _collect_styles(content)
        styles.update({k: v for k, v in _collect_styles(styles_root).items() if k not in styles})

        pages = []
        for index, page in enumerate(content.iter(Q["draw"] + "page"), start=1):
            style_name = _attr(page, "draw", "style-name")
            visibility, basis = _resolve_visibility(style_name, styles)
            notes = page.find(Q["presentation"] + "notes")
            notes_text = _text_of(notes) if notes is not None else ""
            objects = _walk_shapes(page, index, manifest)
            title = ""
            for record in objects:
                if record.presentation_class == "title" and record.text_sample.strip():
                    title = record.text_sample.strip()
                    break
            if not title:
                for record in objects:
                    if record.text_sample.strip():
                        title = record.text_sample.strip().splitlines()[0]
                        break
            counts: dict[str, int] = {}
            for record in objects:
                if record.role != "shape":
                    continue
                counts[record.kind] = counts.get(record.kind, 0) + 1
            pages.append(
                {
                    "source_slide_no": index,
                    "draw_name": _attr(page, "draw", "name"),
                    "drawing_page_style": style_name,
                    "master_page": _attr(page, "draw", "master-page-name"),
                    "visibility": visibility,
                    "visibility_basis": basis,
                    "title_candidate": title,
                    "notes_present": notes is not None,
                    "notes_non_empty": bool(notes_text.strip()),
                    "notes_chars": len(notes_text.strip()),
                    # Kept for the audit trail and the leak check only. It must never
                    # reach a student-facing block, asset or preview.
                    "notes_text": notes_text.strip(),
                    "object_counts": counts,
                    "objects": [record.dump() for record in objects],
                }
            )

        totals: dict[str, int] = {}
        for page in pages:
            for kind, count in page["object_counts"].items():
                totals[kind] = totals.get(kind, 0) + count

        return {
            "file_name": path.name,
            "status": "ok",
            "inspected_at": started,
            "sha256": archive.sha256,
            "size_bytes": archive.size_bytes,
            "zip_entries": len(archive.entries),
            "zip_limit_hits": archive.limit_hits,
            "source_slide_count": len(pages),
            "visible_slide_count": sum(1 for p in pages if p["visibility"] == "visible"),
            "hidden_slide_count": sum(1 for p in pages if p["visibility"] == "hidden"),
            "non_empty_notes_count": sum(1 for p in pages if p["notes_non_empty"]),
            "object_totals": totals,
            "unknown_object_count": totals.get("unknown", 0),
            "manifest_media_types": sorted({m for m in manifest.values() if m}),
            "fonts": _fonts(content) + [f for f in _fonts(styles_root) if f not in _fonts(content)],
            "external_references": _external_refs(content),
            "pages": pages,
        }


def build_inventory(source_dir: Path, limits: ZipLimits | None = None) -> dict:
    files = sorted(Path(source_dir).glob("*.odp"))
    records = [inspect_file(path, limits) for path in files]
    return {
        "generated_at": utc_now(),
        "source_dir": str(source_dir),
        "file_count": len(records),
        "ok_count": sum(1 for r in records if r["status"] == "ok"),
        "failed_count": sum(1 for r in records if r["status"] != "ok"),
        "source_slide_total": sum(r.get("source_slide_count", 0) for r in records),
        "visible_slide_total": sum(r.get("visible_slide_count", 0) for r in records),
        "hidden_slide_total": sum(r.get("hidden_slide_count", 0) for r in records),
        "non_empty_notes_total": sum(r.get("non_empty_notes_count", 0) for r in records),
        "files": records,
    }
