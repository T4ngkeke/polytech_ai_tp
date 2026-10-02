"""A5: the single supplementary variant, docling+native-supplement.

It adds only information that the baseline provably loses, read from the source
file itself:

- table blocks from embedded spreadsheet objects (cells read from the ODF),
- figure blocks for embedded objects and images, with a region crop from our own
  preview page and the labels the drawing carries natively,
- a bbox for blocks whose source object is known, from the slide geometry.

No model rewrites anything. The baseline result is never modified: this variant
is written to its own files.
"""
from __future__ import annotations

import copy
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import sha256_file, sha256_bytes
from .inventory import Q, _text_of

FIGURE_KINDS = {"embedded_graphics", "embedded_image", "group_shape", "embedded_object", "embedded_object_ole"}
MATHML_MIME = "application/mathml+xml"


def slide_size_cm(odp: Path) -> tuple[float, float] | None:
    """Slide width and height from the master page layout."""
    with zipfile.ZipFile(odp) as archive:
        if "styles.xml" not in archive.namelist():
            return None
        root = ET.fromstring(archive.read("styles.xml"))
    for properties in root.iter(Q["style"] + "page-layout-properties"):
        width = properties.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}page-width")
        height = properties.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}page-height")
        if width and height:
            def to_cm(raw: str) -> float:
                value = float(re.match(r"(-?[\d.]+)", raw).group(1))
                unit = raw[len(re.match(r"-?[\d.]+", raw).group(0)):]
                return {"cm": value, "mm": value / 10, "in": value * 2.54, "pt": value * 2.54 / 72}.get(unit, value)
            return to_cm(width), to_cm(height)
    return None


def _normalised_bbox(position: dict, size: tuple[float, float] | None) -> dict | None:
    if not size or not position or position.get("x") is None or position.get("width") is None:
        return None
    width_cm, height_cm = size
    x0 = position["x"] / width_cm
    y0 = position["y"] / height_cm
    x1 = (position["x"] + position["width"]) / width_cm
    y1 = (position["y"] + (position["height"] or 0)) / height_cm
    return {
        "bbox": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
        "coord_unit": "fraction_of_slide",
        "coord_origin": "top_left",
    }


def embedded_table(odp: Path, href: str) -> dict | None:
    """Read an embedded spreadsheet as a grid of cell strings."""
    folder = href.lstrip("./").rstrip("/")
    with zipfile.ZipFile(odp) as archive:
        name = f"{folder}/content.xml"
        if name not in archive.namelist():
            return None
        root = ET.fromstring(archive.read(name))
    rows = []
    for table in root.iter(Q["table"] + "table"):
        for row in table.iter(Q["table"] + "table-row"):
            cells = []
            for cell in row.findall(Q["table"] + "table-cell"):
                repeat = int(cell.attrib.get(Q["table"] + "number-columns-repeated", "1"))
                value = _text_of(cell).strip()
                cells.extend([value] * min(repeat, 64))
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append(cells)
    if not rows:
        return None
    return {"rows": rows, "object_folder": folder}


def embedded_drawing_labels(odp: Path, href: str) -> dict | None:
    """Text labels and shape counts inside an embedded drawing object."""
    folder = href.lstrip("./").rstrip("/")
    with zipfile.ZipFile(odp) as archive:
        name = f"{folder}/content.xml"
        if name not in archive.namelist():
            return None
        root = ET.fromstring(archive.read(name))
    labels = []
    counts: dict[str, int] = {}
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag in {"custom-shape", "g", "line", "connector", "polyline", "path", "ellipse", "circle", "rect", "frame"}:
            counts[tag] = counts.get(tag, 0) + 1
            text = _text_of(element).strip()
            if text and text not in labels:
                labels.append(text)
    return {"labels": labels, "shape_counts": counts, "object_folder": folder}


def embedded_formula(odp: Path, href: str, target: Path) -> dict | None:
    """Save the formula object's own XML as an asset.

    The original is kept as it is. It is not converted to LaTeX and must not be
    presented as if it were.
    """
    folder = href.lstrip("./").rstrip("/")
    with zipfile.ZipFile(odp) as archive:
        name = f"{folder}/content.xml"
        if name not in archive.namelist():
            return None
        data = archive.read(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    text = data.decode("utf-8", "replace")
    annotation = re.search(r"<annotation[^>]*>(.*?)</annotation>", text, re.S)
    return {
        "asset": {
            "path": str(target),
            "sha256": sha256_file(target),
            "mime": MATHML_MIME if "math:math" in text or "<math" in text else "application/xml",
            "asset_kind": "original",
            "object_folder": folder,
        },
        "starmath_annotation": annotation.group(1).strip() if annotation else None,
    }


def _rows_as_text(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in rows)


def crop_region(preview_png: Path, bbox: list[float], target: Path) -> dict | None:
    """Cut the object's area out of our own rendered page."""
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(preview_png) as image:
        width, height = image.size
        box = (
            max(0, int(bbox[0] * width)), max(0, int(bbox[1] * height)),
            min(width, int(bbox[2] * width)), min(height, int(bbox[3] * height)),
        )
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        image.crop(box).save(target)
    return {
        "path": str(target),
        "sha256": sha256_file(target),
        "mime": "image/png",
        "asset_kind": "rendered_region",
        "rendered_from": str(preview_png),
    }


def build_supplement(baseline: dict, inventory_file: dict, odp: Path, *, preview_images: list[dict],
                      asset_dir: Path) -> dict:
    """Return a new normalised document. The baseline argument is not modified."""
    result = copy.deepcopy(baseline)
    result["variant"] = "docling+native-supplement"
    size = slide_size_cm(odp)
    preview_by_page = {image["preview_pdf_page_no"]: Path(image["path"]) for image in preview_images}
    added = {"table_blocks": 0, "figure_blocks": 0, "formula_blocks": 0, "bbox_filled": 0, "region_crops": 0}

    objects_by_path = {
        obj["object_path"]: obj
        for page in inventory_file["pages"] for obj in page["objects"]
    }

    for page in result["pages"]:
        source_page = inventory_file["pages"][page["source_slide_no"] - 1]
        # A bbox for the blocks whose source object we already know.
        for block in page["blocks"]:
            path = block["source_ref"].get("xml_object_path")
            obj = objects_by_path.get(path) if path else None
            if obj is None:
                continue
            geometry = _normalised_bbox(obj["position_cm"], size)
            if geometry:
                block["source_ref"].update(
                    bbox=geometry["bbox"],
                    bbox_coord_unit=geometry["coord_unit"],
                    bbox_coord_origin=geometry["coord_origin"],
                    bbox_extracted_by="native_odf_geometry",
                )
                added["bbox_filled"] += 1

        order = len(page["blocks"])
        for obj in source_page["objects"]:
            if obj["role"] != "shape":
                continue
            geometry = _normalised_bbox(obj["position_cm"], size)
            preview_png = preview_by_page.get(page.get("preview_pdf_page_no"))

            if obj["kind"] == "embedded_spreadsheet" and obj["href"]:
                table = embedded_table(odp, obj["href"])
                if not table:
                    continue
                content = _rows_as_text(table["rows"])
                page["blocks"].append(_native_block(
                    result, page, order, "table", content, obj, geometry,
                    extracted_by="native_odf_embedded_spreadsheet",
                    extra={"table_rows": len(table["rows"]), "object_folder": table["object_folder"]},
                ))
                order += 1
                added["table_blocks"] += 1

            elif obj["kind"] == "embedded_formula" and obj["href"]:
                saved = embedded_formula(
                    odp, obj["href"],
                    asset_dir / f"{Path(odp).stem}-p{page['source_slide_no']:03d}-formula{order:03d}.xml",
                )
                if not saved:
                    continue
                block = _native_block(
                    result, page, order, "formula", saved["starmath_annotation"] or "", obj, geometry,
                    extracted_by="native_odf_formula_object",
                    extra={"formula_source": "original object XML kept as an asset"},
                )
                saved["asset"]["source_object_path"] = obj["object_path"]
                block["assets"] = [saved["asset"]]
                block["format"] = "plain"
                block["issues"].append({
                    "code": "content_review",
                    "severity": "warning",
                    "stage": "supplement",
                    "message": "the formula original is kept as an asset; it is not converted to LaTeX",
                    "blocks_release": False,
                })
                block["completeness"]["extraction_status"] = "partial"
                page["blocks"].append(block)
                order += 1
                added["formula_blocks"] = added.get("formula_blocks", 0) + 1

            elif obj["kind"] in FIGURE_KINDS:
                labels = embedded_drawing_labels(odp, obj["href"]) if obj["href"] else None
                assets = []
                if geometry and preview_png and preview_png.exists():
                    crop = crop_region(
                        preview_png, geometry["bbox"],
                        asset_dir / f"{Path(odp).stem}-p{page['source_slide_no']:03d}-o{order:03d}.png",
                    )
                    if crop:
                        crop["source_object_path"] = obj["object_path"]
                        assets.append(crop)
                        added["region_crops"] += 1
                content = "\n".join(labels["labels"]) if labels and labels["labels"] else ""
                block = _native_block(
                    result, page, order, "figure", content, obj, geometry,
                    extracted_by="native_odf_embedded_object",
                    extra={
                        "drawing_shape_counts": labels["shape_counts"] if labels else None,
                        "label_count": len(labels["labels"]) if labels else 0,
                    },
                )
                block["assets"] = assets
                block["issues"].append({
                    "code": "content_review",
                    "severity": "warning",
                    "stage": "supplement",
                    "message": (
                        "labels and a region crop only: the drawing's connections and directions are not extracted"
                    ),
                    "blocks_release": False,
                })
                page["blocks"].append(block)
                order += 1
                added["figure_blocks"] += 1

    result["totals"]["blocks"] = sum(len(p["blocks"]) for p in result["pages"])
    result["totals"]["blocks_with_source_object"] = sum(
        1 for p in result["pages"] for b in p["blocks"] if b["source_ref"]["mapping_status"] == "mapped"
    )
    result["totals"]["blocks_with_bbox"] = sum(
        1 for p in result["pages"] for b in p["blocks"] if b["source_ref"].get("bbox")
    )
    result["supplement_added"] = added
    return result


def _native_block(document: dict, page: dict, order: int, kind: str, content: str, obj: dict,
                   geometry: dict | None, *, extracted_by: str, extra: dict) -> dict:
    return {
        "block_id": f"{document['source_sha256'][:12]}-p{page['source_slide_no']:03d}-s{order:03d}",
        "kind": kind,
        "format": "plain",
        "content": content,
        "content_sha256": sha256_bytes(content.encode("utf-8")) if content else None,
        "docling_label": None,
        "container_path": [],
        "source_ref": {
            "file_sha256": document["source_sha256"],
            "source_slide_no": page["source_slide_no"],
            "slide_no_method": "native_odf_slide_index",
            "slide_no_status": "read_from_source",
            "xml_object_path": obj["object_path"],
            "source_paragraph_no": None,
            "native_id": obj["xml_id"],
            "native_draw_name": obj["draw_name"],
            "docling_item_ref": None,
            "bbox": geometry["bbox"] if geometry else None,
            "bbox_coord_unit": geometry["coord_unit"] if geometry else None,
            "bbox_coord_origin": geometry["coord_origin"] if geometry else None,
            "mapping_method": "native_odf_object",
            "mapping_status": "mapped",
            "candidate_count": 1,
            "extracted_by": extracted_by,
        },
        "assets": [],
        "descriptions": {"content_description": None, "context_description": None, "description_origin": "none"},
        "completeness": {
            "source_status": "unknown",
            "extraction_status": "partial" if kind == "figure" else "complete",
            "extraction_evidence": f"read from the source file by {extracted_by}",
            "returned_fragment": False,
        },
        "links": [],
        "issues": [],
        "extra": extra,
    }
