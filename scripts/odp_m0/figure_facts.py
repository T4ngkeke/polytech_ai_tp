"""Read the truth about a drawing out of the source file, not out of a picture.

An embedded ODG object holds its nodes, its connectors, the text on each of
them and the styles that colour them. Reading that gives a fact list that check
points can be written from before any model is called, and it is exact where
looking at a rendered picture is a judgement.

What this does not give: what the colours are supposed to mean. The drawings in
this courseware carry no legend, so the meaning of red or green lives in the
slide text, not in the drawing. That is recorded as unknown here on purpose.
"""
from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import NS, M0Error
from .inventory import Q, _text_of

NODE_TAGS = {"draw:custom-shape", "draw:ellipse", "draw:circle", "draw:rect"}
EDGE_TAGS = {"draw:connector", "draw:line", "draw:polyline", "draw:path"}


def _prefixed(tag: str) -> str:
    for prefix, uri in NS.items():
        if tag.startswith("{" + uri + "}"):
            return f"{prefix}:{tag.split('}')[1]}"
    return tag


def _cm(raw: str | None) -> float | None:
    if not raw:
        return None
    match = re.fullmatch(r"(-?[\d.]+)(cm|mm|in|pt)?", raw.strip())
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2) or "cm"
    return {"cm": value, "mm": value / 10, "in": value * 2.54, "pt": value * 2.54 / 72}[unit]


def object_href(odp: Path, slide_no: int, object_path: str) -> str | None:
    """The embedded object a figure frame points at."""
    tail = object_path.rsplit("/", 1)[-1]
    name, _, index = tail.partition("[")
    index = int(index.rstrip("]") or 1)
    with zipfile.ZipFile(odp) as archive:
        content = ET.fromstring(archive.read("content.xml"))
    page = content.findall(".//" + Q["draw"] + "page")[slide_no - 1]
    counts: dict[str, int] = {}
    for child in page:
        prefixed = _prefixed(child.tag)
        if prefixed in ("presentation:notes", "office:forms"):
            continue
        counts[prefixed] = counts.get(prefixed, 0) + 1
        if prefixed == name and counts[prefixed] == index:
            for inner in child:
                if _prefixed(inner.tag) == "draw:object":
                    return inner.attrib.get(Q["xlink"] + "href")
            return None
    return None


def _styles(root: ET.Element) -> dict:
    out = {}
    for style in root.iter(Q["style"] + "style"):
        name = style.attrib.get(Q["style"] + "name")
        if not name:
            continue
        entry = {"fill_color": None, "stroke_color": None, "marker_start": None, "marker_end": None,
                 "parent": style.attrib.get(Q["style"] + "parent-style-name")}
        for properties in style:
            entry["fill_color"] = properties.attrib.get(Q["draw"] + "fill-color") or entry["fill_color"]
            entry["stroke_color"] = properties.attrib.get(Q["svg"] + "stroke-color") or entry["stroke_color"]
            entry["marker_start"] = properties.attrib.get(Q["draw"] + "marker-start") or entry["marker_start"]
            entry["marker_end"] = properties.attrib.get(Q["draw"] + "marker-end") or entry["marker_end"]
        out[name] = entry
    return out


def _resolve(style_name: str | None, styles: dict, field: str):
    seen = set()
    name = style_name
    while name and name in styles and name not in seen:
        seen.add(name)
        value = styles[name].get(field)
        if value:
            return value
        name = styles[name].get("parent")
    return None


def read_drawing(odp: Path, href: str) -> dict:
    """Nodes, edges and their colours, straight from the embedded drawing."""
    folder = href.lstrip("./").rstrip("/")
    with zipfile.ZipFile(odp) as archive:
        name = f"{folder}/content.xml"
        if name not in archive.namelist():
            raise M0Error(f"{odp.name}: {folder} has no content.xml")
        root = ET.fromstring(archive.read(name))
    styles = _styles(root)

    nodes, edges, other = [], [], []
    for element in root.iter():
        prefixed = _prefixed(element.tag)
        text = _text_of(element).strip()
        style_name = element.attrib.get(Q["draw"] + "style-name")
        if prefixed in NODE_TAGS:
            x, y = _cm(element.attrib.get(Q["svg"] + "x")), _cm(element.attrib.get(Q["svg"] + "y"))
            width = _cm(element.attrib.get(Q["svg"] + "width")) or 0
            height = _cm(element.attrib.get(Q["svg"] + "height")) or 0
            if x is None or y is None:
                continue
            nodes.append({
                "label": text, "centre": [round(x + width / 2, 3), round(y + height / 2, 3)],
                "fill_color": _resolve(style_name, styles, "fill_color"), "style": style_name,
            })
        elif prefixed in EDGE_TAGS:
            x1, y1 = _cm(element.attrib.get(Q["svg"] + "x1")), _cm(element.attrib.get(Q["svg"] + "y1"))
            x2, y2 = _cm(element.attrib.get(Q["svg"] + "x2")), _cm(element.attrib.get(Q["svg"] + "y2"))
            if None in (x1, y1, x2, y2):
                continue
            edges.append({
                "label": text, "from_point": [x1, y1], "to_point": [x2, y2],
                "stroke_color": _resolve(style_name, styles, "stroke_color"),
                "marker_start": _resolve(style_name, styles, "marker_start"),
                "marker_end": _resolve(style_name, styles, "marker_end"),
                "style": style_name,
            })
        elif prefixed in ("draw:frame", "draw:g") and text:
            x, y = _cm(element.attrib.get(Q["svg"] + "x")), _cm(element.attrib.get(Q["svg"] + "y"))
            width = _cm(element.attrib.get(Q["svg"] + "width")) or 0
            height = _cm(element.attrib.get(Q["svg"] + "height")) or 0
            other.append({"tag": prefixed, "text": text,
                          "centre": [round((x or 0) + width / 2, 3), round((y or 0) + height / 2, 3)]
                          if x is not None and y is not None else None})

    # Many drawings here label nothing: the nodes are plain coloured circles. Give
    # each one a stable name from its place on the drawing, top to bottom then
    # left to right, so facts can be written about a drawing without labels.
    ordered = sorted(range(len(nodes)), key=lambda i: (round(nodes[i]["centre"][1], 1), nodes[i]["centre"][0]))
    for rank, index in enumerate(ordered, start=1):
        nodes[index]["node_id"] = f"N{rank}"
        nodes[index]["name"] = nodes[index]["label"] or f"N{rank}"

    # A node often carries two names: the value drawn inside it and an identifier
    # label placed next to it, like node0. Both name the same node, so both are
    # kept and either one counts as naming it correctly.
    for label in other:
        if not label.get("centre"):
            continue
        near = _nearest(label["centre"], nodes, limit=2.2)
        if near is not None:
            near.setdefault("aliases", [])
            if label["text"] not in near["aliases"]:
                near["aliases"].append(label["text"])

    for edge in edges:
        start = _nearest(edge["from_point"], nodes)
        end = _nearest(edge["to_point"], nodes)
        edge["from_node"] = start["name"] if start else None
        edge["to_node"] = end["name"] if end else None
        edge["from_id"] = start["node_id"] if start else None
        edge["to_id"] = end["node_id"] if end else None
        edge["directed"] = bool(edge["marker_end"] or edge["marker_start"])
        if edge["marker_start"] and not edge["marker_end"]:
            # The arrow head sits on the first point, so the line is drawn
            # towards that end: the reading direction is the other way round.
            edge["from_node"], edge["to_node"] = edge["to_node"], edge["from_node"]
            edge["from_id"], edge["to_id"] = edge["to_id"], edge["from_id"]
            edge["arrow_on"] = "start_point"
        if start is not None and end is not None and start is end:
            # Both ends landed on the same node: the match is not trustworthy.
            edge["from_node"] = edge["to_node"] = None
            edge["from_id"] = edge["to_id"] = None
            edge["match"] = "both ends matched the same node"

    return {
        "object": folder,
        "nodes": nodes,
        "edges": edges,
        "other_text": other,
        "directed_graph": any(e["directed"] for e in edges),
        "node_labels": sorted({n["label"] for n in nodes if n["label"]}),
        "node_count": len(nodes),
        "colour_groups": _colour_groups(nodes),
        "legend_present": False,
        "colour_meaning": "unknown: the drawing carries no legend, the meaning is in the slide text",
    }


def _nearest(point: list[float], nodes: list[dict], limit: float = 1.5) -> dict | None:
    best, best_distance = None, None
    for node in nodes:
        distance = math.dist(point, node["centre"])
        if best_distance is None or distance < best_distance:
            best, best_distance = node, distance
    # A connector endpoint sits on the node's rim, so allow roughly one node away.
    return best if best is not None and best_distance is not None and best_distance < limit else None


def _colour_groups(nodes: list[dict]) -> dict:
    groups: dict[str, list[str]] = {}
    for node in nodes:
        groups.setdefault(node["fill_color"] or "default", []).append(node.get("name") or node["label"])
    return {colour: sorted(labels) for colour, labels in groups.items()}


def facts_for(odp: Path, slide_no: int, object_path: str) -> dict:
    href = object_href(odp, slide_no, object_path)
    if not href:
        return {"status": "no_embedded_object", "reason": "this figure frame points at no embedded object"}
    drawing = read_drawing(odp, href)
    drawing["status"] = "ok"
    edges = []
    for edge in drawing["edges"]:
        if edge["from_node"] and edge["to_node"]:
            if edge["directed"]:
                # An arrow has a direction, so it is recorded as drawn.
                pair = [edge["from_node"], edge["to_node"]]
            else:
                pair = sorted([edge["from_node"], edge["to_node"]])
            ids = ([edge["from_id"], edge["to_id"]] if edge["directed"]
                   else sorted([edge["from_id"], edge["to_id"]]))
            edges.append({"between": pair, "between_ids": ids, "directed": edge["directed"],
                          "weight_label": edge["label"], "stroke_color": edge["stroke_color"]})
    drawing["edge_facts"] = edges
    drawing["unmatched_edges"] = sum(1 for e in drawing["edges"] if not (e["from_node"] and e["to_node"]))
    drawing["labels_present"] = any(n["label"] or n.get("aliases") for n in drawing["nodes"])
    named = [n["label"] for n in drawing["nodes"] if n["label"]]
    # Two nodes with the same text cannot be told apart by name. It happens when a
    # slide puts two diagrams side by side, and it makes node by node matching
    # meaningless: such a drawing is judged on counts and direction only.
    drawing["duplicate_labels"] = len(named) != len(set(named))
    drawing["name_to_id"] = {
        name.strip().lower(): n["node_id"]
        for n in drawing["nodes"]
        for name in ([n["label"]] + (n.get("aliases") or []) + [n.get("name", "")])
        if name and name.strip()
    }
    drawing["names_by_node"] = [
        {"node_id": n["node_id"], "primary": n.get("name") or n["label"],
         "accepted_names": sorted({x for x in ([n["label"]] + (n.get("aliases") or [])) if x}
                                   or {n.get("name", "")})}
        for n in drawing["nodes"]
    ]
    return drawing
