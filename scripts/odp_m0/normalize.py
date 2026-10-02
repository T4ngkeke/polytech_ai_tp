"""A4: map the raw Docling result onto the experimental complete-block schema.

This is a projection of the roadmap schema for the experiment, not a migration.
Anything Docling does not supply stays unknown: a missing page number is derived
from the slide group order and marked as derived, a missing bbox stays null, and
a Docling item that cannot be tied to one source object is ambiguous or
unmapped, never merged on the strength of equal text.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import SCHEMA_VERSION, sha256_bytes

# Docling labels to roadmap kinds. A label we do not know leaves kind null.
KIND_BY_LABEL = {
    "title": "heading",
    "section_header": "heading",
    "paragraph": "text",
    "text": "text",
    "list_item": "text",
    "caption": "text",
    "code": "code",
    "formula": "formula",
    "table": "table",
    "picture": "figure",
    "chart": "figure",
    "page_header": "navigation",
    "page_footer": "navigation",
}


def _normalise_for_match(text: str) -> str:
    """Collapse whitespace for matching only. Stored content keeps every character."""
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _source_paragraph_index(inventory_page: dict) -> dict:
    """Paragraph text -> the source objects that contain it, for this slide only."""
    index: dict[str, list[dict]] = {}
    for obj in inventory_page["objects"]:
        if obj["role"] != "shape" or not obj["text_sample"].strip():
            continue
        for position, line in enumerate(obj["text_sample"].splitlines(), start=1):
            key = _normalise_for_match(line)
            if not key:
                continue
            index.setdefault(key, []).append({
                "object_path": obj["object_path"],
                "paragraph_no": position,
                "xml_id": obj["xml_id"],
                "draw_name": obj["draw_name"],
                "kind": obj["kind"],
            })
    return index


def _resolve_source_ref(text: str, index: dict, file_sha: str, slide_no: int, item_ref: str) -> dict:
    """Tie one Docling item to a source object, or say honestly that we cannot."""
    key = _normalise_for_match(text)
    candidates = index.get(key, [])
    if len(candidates) == 1:
        status, method, chosen = "mapped", "unique_paragraph_text", candidates[0]
    elif len(candidates) > 1:
        status, method, chosen = "ambiguous", "repeated_paragraph_text", None
    else:
        status, method, chosen = "unmapped", "no_matching_source_paragraph", None
    return {
        "file_sha256": file_sha,
        "source_slide_no": slide_no,
        "slide_no_method": "docling_slide_group_order",
        "slide_no_status": "derived_from_structure",
        "xml_object_path": chosen["object_path"] if chosen else None,
        "source_paragraph_no": chosen["paragraph_no"] if chosen else None,
        "native_id": (chosen or {}).get("xml_id"),
        "native_draw_name": (chosen or {}).get("draw_name"),
        "docling_item_ref": item_ref,
        "bbox": None,
        "bbox_coord_unit": None,
        "bbox_coord_origin": None,
        "mapping_method": method,
        "mapping_status": status,
        "candidate_count": len(candidates),
    }


def _iter_slide_items(raw: dict, group_ref: str, *, merge_inline: bool = False):
    """Walk one slide group in document order, flattening nested list and group nodes.

    With merge_inline, a group labelled "inline" is joined back into a single
    item. Docling emits one text item per styled run but keeps the run's
    paragraph in an inline group, so joining them restores the source line. The
    parts are concatenated as they are: Docling carries the spacing inside the
    runs, and adding a separator would invent characters.
    """
    by_ref = {}
    for bucket in ("groups", "texts", "pictures", "tables"):
        for item in raw.get(bucket) or []:
            by_ref[item["self_ref"]] = (bucket, item)

    def collect_inline(ref: str) -> tuple[str, list[str], str | None]:
        """Text of one inline group, its item refs and the label to keep."""
        parts: list[str] = []
        refs: list[str] = []
        label: str | None = None
        def walk_inline(inner: str):
            nonlocal label
            bucket, item = by_ref[inner]
            if bucket == "groups":
                for child in item.get("children") or []:
                    walk_inline(child["$ref"])
                return
            parts.append(item.get("text") or "")
            refs.append(item["self_ref"])
            if label is None:
                label = item.get("label")
            for child in item.get("children") or []:
                walk_inline(child["$ref"])
        walk_inline(ref)
        return "".join(parts), refs, label

    def walk(ref: str, container_path: list[str]):
        bucket, item = by_ref[ref]
        if bucket == "groups":
            if merge_inline and item.get("label") == "inline":
                text, refs, label = collect_inline(ref)
                if text:
                    merged = {
                        "self_ref": refs[0] if refs else item["self_ref"],
                        "label": label or "text",
                        "text": text,
                        "prov": [],
                        "children": [],
                        "merged_from": refs,
                    }
                    yield "texts", merged, container_path + ["inline"]
                return
            path = container_path + [item.get("name") or item.get("label") or "group"]
            for child in item.get("children") or []:
                yield from walk(child["$ref"], path)
            return
        yield bucket, item, container_path
        # A text item can itself have children: Docling hangs a nested list under
        # the list item above it, not under the surrounding group.
        for child in item.get("children") or []:
            yield from walk(child["$ref"], container_path + [item.get("label") or "item"])

    for child in by_ref[group_ref][1].get("children") or []:
        yield from walk(child["$ref"], [])


def normalize_document(raw: dict, inventory_file: dict, probe: dict, *, run_id: str, parser: str,
                        parser_version: str, merge_inline: bool = False, variant: str | None = None) -> dict:
    """Build normalized.json for one document."""
    file_sha = inventory_file["sha256"]
    slide_groups = []
    for child in raw["body"].get("children") or []:
        ref = child["$ref"]
        group = next((g for g in raw.get("groups") or [] if g["self_ref"] == ref), None)
        if group is not None and (group.get("name") or "").startswith("slide-"):
            slide_groups.append(group)

    issues: list[dict] = []
    if len(slide_groups) != inventory_file["source_slide_count"]:
        issues.append({
            "code": "preview_mapping_failed",
            "severity": "blocking",
            "stage": "normalize",
            "message": (
                f"Docling produced {len(slide_groups)} slide groups for "
                f"{inventory_file['source_slide_count']} source slides"
            ),
            "blocks_release": True,
        })

    visible_no = 0
    pages = []
    for position, group in enumerate(slide_groups, start=1):
        inventory_page = inventory_file["pages"][position - 1] if position <= len(inventory_file["pages"]) else None
        visibility = inventory_page["visibility"] if inventory_page else "unknown"
        if visibility == "visible":
            visible_no += 1
        index = _source_paragraph_index(inventory_page) if inventory_page else {}
        blocks = []
        for order, (bucket, item, container_path) in enumerate(
            _iter_slide_items(raw, group["self_ref"], merge_inline=merge_inline)
        ):
            label = item.get("label")
            kind = KIND_BY_LABEL.get(label)
            text = item.get("text") or ""
            block_issues = []
            if kind is None:
                block_issues.append({
                    "code": "content_review",
                    "severity": "warning",
                    "stage": "normalize",
                    "message": f"no kind for Docling label {label!r}",
                    "blocks_release": False,
                })
            source_ref = _resolve_source_ref(text, index, file_sha, position, item["self_ref"])
            if bucket == "pictures":
                source_ref["mapping_method"] = "not_attempted_for_picture"
                source_ref["mapping_status"] = "unmapped"
                block_issues.append({
                    "code": "source_identity_conflict",
                    "severity": "blocking",
                    "stage": "normalize",
                    "message": "Docling gives no source object or position for a picture",
                    "blocks_release": True,
                })
            elif source_ref["mapping_status"] != "mapped":
                block_issues.append({
                    "code": "source_identity_conflict",
                    "severity": "warning",
                    "stage": "normalize",
                    "message": f"source object {source_ref['mapping_status']} ({source_ref['candidate_count']} candidates)",
                    "blocks_release": False,
                })

            assets = []
            if bucket == "pictures":
                match = next((a for a in probe.get("assets") or [] if a.get("item_ref") == item["self_ref"]), None)
                if match and match.get("path"):
                    assets.append({
                        "path": match["path"],
                        "sha256": match["sha256"],
                        "mime": match["mime"],
                        "source_object_path": None,
                        "asset_kind": "original",
                    })

            extraction_status = "complete" if source_ref["mapping_status"] == "mapped" else "unknown"
            blocks.append({
                "block_id": f"{file_sha[:12]}-p{position:03d}-b{order:03d}",
                "merged_from_item_refs": item.get("merged_from"),
                "kind": kind,
                "format": "plain",
                "content": text,
                "content_sha256": sha256_bytes(text.encode("utf-8")) if text else None,
                "docling_label": label,
                "container_path": container_path,
                "source_ref": source_ref,
                "assets": assets,
                "descriptions": {
                    "content_description": None,
                    "context_description": None,
                    "description_origin": "none",
                },
                "completeness": {
                    "source_status": "unknown",
                    "extraction_status": extraction_status,
                    "extraction_evidence": (
                        "content equals one source paragraph" if extraction_status == "complete" else None
                    ),
                    "returned_fragment": False,
                },
                "links": [],
                "issues": block_issues,
            })

        pages.append({
            "source_slide_no": position,
            "visible_page_no": visible_no if visibility == "visible" else None,
            "preview_pdf_page_no": None,
            "visibility": visibility,
            "visibility_basis": inventory_page["visibility_basis"] if inventory_page else "unknown",
            "blocks": blocks,
            "issues": [],
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "source_path": inventory_file["file_name"],
        "source_sha256": file_sha,
        "parser": parser,
        "parser_version": parser_version,
        "variant": variant or ("docling-native-inline-merged" if merge_inline else "docling-native-baseline"),
        "pages": pages,
        "issues": issues,
        "totals": {
            "pages": len(pages),
            "blocks": sum(len(p["blocks"]) for p in pages),
            "blocks_with_source_object": sum(
                1 for p in pages for b in p["blocks"] if b["source_ref"]["mapping_status"] == "mapped"
            ),
            "blocks_without_kind": sum(1 for p in pages for b in p["blocks"] if b["kind"] is None),
            "blocks_with_bbox": 0,
        },
    }


def attach_preview_mapping(normalized: dict, mapping: list[dict]) -> dict:
    """Fill preview_pdf_page_no from the verified render mapping."""
    by_slide = {entry["source_slide_no"]: entry for entry in mapping}
    for page in normalized["pages"]:
        entry = by_slide.get(page["source_slide_no"])
        if not entry:
            continue
        page["preview_pdf_page_no"] = entry["preview_pdf_page_no"]
        page["preview_mapping_check"] = entry["check"]
    return normalized
