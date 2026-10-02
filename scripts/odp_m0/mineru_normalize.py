"""Map a MinerU native ODP result onto the m0-odp-1 experimental schema.

MinerU returns one item per source paragraph, with the styled runs as parts of
that item, so paragraphs need no reassembly. What it does not return is a page
coordinate or a source object id; those stay null and are recorded as missing
rather than derived silently.
"""
from __future__ import annotations

import re

from .common import SCHEMA_VERSION, sha256_bytes
from .mineru_probe import item_text
from .normalize import _resolve_source_ref, _source_paragraph_index

KIND_BY_TYPE = {
    "doc_title": "heading",
    "paragraph_title": "heading",
    "text": "text",
    "list": "text",
    "image": "figure",
    "table": "table",
    "code": "code",
    "formula": "formula",
}


def _flatten(item: dict, depth: int = 0):
    """Yield (type, text, depth, item) in reading order, unrolling nested lists."""
    kind = str(item.get("type", "")).split(".")[-1].lower()
    if kind == "list":
        for child in item.get("content") or []:
            if isinstance(child, dict):
                yield from _flatten(child, depth + 1)
        return
    yield kind, item_text(item), depth, item


def _list_items(item: dict, depth: int = 1):
    """A list's entries, one block each, keeping the nesting level."""
    for child in item.get("content") or []:
        if not isinstance(child, dict):
            continue
        child_kind = str(child.get("type", "")).split(".")[-1].lower()
        if child_kind == "list":
            yield from _list_items(child, depth + 1)
        else:
            yield child_kind, item_text(child), depth, child


def iter_page_items(page: list[dict]):
    """Depth 0 means the item is not inside a list; a top level list entry is 1."""
    for item in page:
        kind = str(item.get("type", "")).split(".")[-1].lower()
        if kind == "list":
            yield from _list_items(item, 1)
        else:
            yield kind, item_text(item), 0, item


def _styles(item: dict) -> list[str]:
    found = set()
    def walk(node):
        if isinstance(node, dict):
            for style in node.get("styles") or []:
                found.add(style)
            content = node.get("content")
            if isinstance(content, list):
                for part in content:
                    walk(part)
    walk(item)
    return sorted(found)


def normalize_document(raw: dict, inventory_file: dict, probe: dict, *, run_id: str,
                        parser: str = "mineru-odp-native", parser_version: str = "4.0.2") -> dict:
    file_sha = inventory_file["sha256"]
    pages_raw = raw["pages"]
    issues: list[dict] = []
    if len(pages_raw) != inventory_file["source_slide_count"]:
        issues.append({
            "code": "preview_mapping_failed", "severity": "blocking", "stage": "normalize",
            "message": f"MinerU returned {len(pages_raw)} slides for {inventory_file['source_slide_count']} source slides",
            "blocks_release": True,
        })

    assets_by_page: dict[int, list[dict]] = {}
    for asset in probe.get("assets") or []:
        assets_by_page.setdefault(asset["page_no"], []).append(asset)

    visible_no = 0
    pages = []
    for position, page_raw in enumerate(pages_raw, start=1):
        inventory_page = inventory_file["pages"][position - 1] if position <= len(inventory_file["pages"]) else None
        visibility = inventory_page["visibility"] if inventory_page else "unknown"
        if visibility == "visible":
            visible_no += 1
        index = _source_paragraph_index(inventory_page) if inventory_page else {}
        used_assets = {a["item_index"]: a for a in assets_by_page.get(position, [])}

        blocks = []
        for order, (item_type, text, depth, item) in enumerate(iter_page_items(page_raw)):
            kind = KIND_BY_TYPE.get(item_type)
            block_issues = []
            if kind is None:
                block_issues.append({
                    "code": "content_review", "severity": "warning", "stage": "normalize",
                    "message": f"no kind for MinerU type {item_type!r}", "blocks_release": False,
                })

            source_ref = _resolve_source_ref(text, index, file_sha, position, f"#/pages/{position}/items/{order}")
            source_ref["slide_no_method"] = "mineru_slide_grouping"
            source_ref["slide_no_status"] = "reported_by_parser"
            source_ref["bbox_status"] = "not_provided_by_parser"

            assets = []
            if kind == "figure":
                match = next((a for a in used_assets.values() if a.get("item_index") is not None
                              and a["path"].endswith(item.get("asset_path", "\0").split("/")[-1])), None)
                if match:
                    assets.append({
                        "path": match["path"], "sha256": match["sha256"], "mime": match["mime"],
                        "width": match.get("width"), "height": match.get("height"),
                        "decodable": match.get("decodable"),
                        "source_object_path": None, "asset_kind": "original",
                    })
                source_ref["mapping_method"] = "not_provided_for_image"
                source_ref["mapping_status"] = "unmapped"
                block_issues.append({
                    "code": "source_identity_conflict", "severity": "blocking", "stage": "normalize",
                    "message": "MinerU gives no source object for an image; the binding has to be established elsewhere",
                    "blocks_release": True,
                })
            elif source_ref["mapping_status"] != "mapped":
                block_issues.append({
                    "code": "source_identity_conflict", "severity": "warning", "stage": "normalize",
                    "message": f"source object {source_ref['mapping_status']} ({source_ref['candidate_count']} candidates)",
                    "blocks_release": False,
                })

            blocks.append({
                "block_id": f"{file_sha[:12]}-p{position:03d}-m{order:03d}",
                "kind": kind,
                "format": "plain",
                "content": text,
                "content_sha256": sha256_bytes(text.encode("utf-8")) if text else None,
                "parser_type": item_type,
                "list_depth": depth,
                "styles": _styles(item),
                "container_path": ["list"] * depth,
                "source_ref": source_ref,
                "assets": assets,
                "descriptions": {"content_description": None, "context_description": None, "description_origin": "none"},
                "completeness": {
                    "source_status": "unknown",
                    "extraction_status": "complete" if source_ref["mapping_status"] == "mapped" else "unknown",
                    "extraction_evidence": ("content equals one source paragraph"
                                            if source_ref["mapping_status"] == "mapped" else None),
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
        "variant": "mineru-native-baseline",
        "pages": pages,
        "issues": issues,
        "totals": {
            "pages": len(pages),
            "blocks": sum(len(p["blocks"]) for p in pages),
            "blocks_with_source_object": sum(
                1 for p in pages for b in p["blocks"] if b["source_ref"]["mapping_status"] == "mapped"),
            "blocks_without_kind": sum(1 for p in pages for b in p["blocks"] if b["kind"] is None),
            "blocks_with_bbox": 0,
            "figure_blocks": sum(1 for p in pages for b in p["blocks"] if b["kind"] == "figure"),
            "table_blocks": sum(1 for p in pages for b in p["blocks"] if b["kind"] == "table"),
        },
    }
