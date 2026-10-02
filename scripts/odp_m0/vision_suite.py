"""The frozen figure set for the vision-benefit round, and its shared input.

Twelve figure objects picked from the source courseware, never from a model's
output. Nine are used while the method is being set up; three are kept back and
scored once at the end.

The shared input is built before any model is called: native text with its
whitespace, native table cells, the true slide number and object position, and a
picture of the figure. A crop that lost part of the drawing is recorded as a
failed extraction and its object is not used to judge the model.
"""
from __future__ import annotations

from pathlib import Path

from .common import sha256_file, utc_now

# (deck, slide, object path tail, category, why this one)
TARGETS = [
    # Direction and weight: the graph algorithms.
    ("Algorithme_Graphe_2022-2023.odp", 4, "draw:frame[3]", "direction_weight",
     "Dijkstra initialisation: weighted graph, the question asks which edge weights are shown"),
    ("Algorithme_Graphe_2022-2023.odp", 6, "draw:frame[3]", "direction_weight",
     "Dijkstra step 2: node extraction, distances marked on the nodes"),
    ("Algorithme_Graphe_2022-2023.odp", 16, "draw:frame[3]", "direction_weight",
     "Prim recursive step: the minimum weight edge joining the tree"),
    ("Algorithme_Graphe_2022-2023.odp", 23, "draw:frame[3]", "direction_weight",
     "Kruskal: the first edge taken from the sorted list"),
    # Trees, object diagrams and grouped shapes.
    ("Arbres_Naires_2022-2023.odp", 7, "draw:frame[3]", "tree_object",
     "recursive representation: an object diagram with named pointers"),
    ("Arbres_Naires_2022-2023.odp", 3, "draw:frame[3]", "tree_object",
     "a binary tree where each node has at most two children"),
    ("Algorithmes_Arborescences_2022-2023.odp", 5, "draw:frame[4]", "tree_object",
     "depth first traversal, a step in the walk"),
    # Only readable together with the title or the legend.
    ("Arbres_Naires_2022-2023.odp", 4, "draw:frame[3]", "needs_context",
     "balanced binary tree: the drawing alone does not say it is balanced"),
    ("Arbres_Naires_2022-2023.odp", 18, "draw:frame[2]", "needs_context",
     "a degenerate search tree: only the title says it is unbalanced"),
    # Held back: scored once, after the method is fixed.
    ("Algorithmes_Arborescences_2022-2023.odp", 13, "draw:frame[4]", "holdout",
     "breadth first traversal"),
    ("Algorithmes_Arborescences_2022-2023.odp", 17, "draw:frame[4]", "holdout",
     "breadth first traversal, a later step"),
    ("Primitives_GrapheEtArbre_2022-2023.odp", 1, "draw:frame[2]", "holdout",
     "wide cover drawing; its frame starts off the left edge of the slide"),
]

# Protection checks: these must come through the vision round unchanged.
PROTECTED_CODE = [
    ("CoursInfoDeBase-2022_SQL.odp", 29),
    ("CoursInfoDeBase-2022_SQL.odp", 35),
    ("Algebre_relationnelle_2020.odp", 10),
]
PROTECTED_TABLES = [
    ("Algebre_relationnelle_2020.odp", 6),
    ("Algebre_relationnelle_2020.odp", 13),
]

CROP_MARGIN = 0.02  # a little room so a stroke on the edge is not clipped
MAX_MARGIN = 0.10   # a drawing may spill past its own frame, so the crop can grow
EDGE_BAND_PX = 3    # how deep to look along each side for content touching the edge
EDGE_TOLERANCE = 28  # how far from the corner colour still counts as background


def target_id(deck: str, slide: int, tail: str) -> str:
    return f"{Path(deck).stem}-p{slide:02d}-{tail.replace(':', '_').replace('[', '').replace(']', '')}"


def find_object(record: dict, slide: int, tail: str) -> dict | None:
    page = record["pages"][slide - 1]
    for obj in page["objects"]:
        if obj["role"] == "shape" and obj["object_path"].endswith(tail):
            return obj
    return None


def _sides_touching_content(image) -> list[str]:
    """Which sides still have drawing on them, meaning the crop cut through it.

    The corner colour is taken as the background. A side counts as touched when
    a pixel near that edge differs from it clearly.
    """
    pixels = image.convert("RGB")
    width, height = pixels.size
    corners = [pixels.getpixel(p) for p in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))]
    background = tuple(sum(channel) // len(corners) for channel in zip(*corners))

    def differs(pixel) -> bool:
        return sum(abs(a - b) for a, b in zip(pixel, background)) > EDGE_TOLERANCE

    touched = []
    for side in ("left", "right", "top", "bottom"):
        hits = 0
        if side in ("left", "right"):
            column_range = range(EDGE_BAND_PX) if side == "left" else range(width - EDGE_BAND_PX, width)
            for x in column_range:
                for y in range(height):
                    if differs(pixels.getpixel((x, y))):
                        hits += 1
        else:
            row_range = range(EDGE_BAND_PX) if side == "top" else range(height - EDGE_BAND_PX, height)
            for y in row_range:
                for x in range(width):
                    if differs(pixels.getpixel((x, y))):
                        hits += 1
        if hits > 2:
            touched.append(side)
    return touched


def crop_figure(preview_png: Path, obj: dict, slide_size_cm: tuple[float, float], target: Path) -> dict:
    """Cut the figure out of the rendered page and prove nothing was cut through.

    A drawing may spill past its own frame, so the crop grows until no drawing
    touches an edge, or until it reaches the slide border. A crop that still has
    content on an edge is a failed extraction: it is kept, marked, and not used
    to judge a model.
    """
    from PIL import Image

    position = obj.get("position_cm") or {}
    if position.get("x") is None or not position.get("width"):
        return {"status": "failed", "reason": "the source object carries no geometry, so it cannot be located"}

    width_cm, height_cm = slide_size_cm
    attempts = []
    margin = CROP_MARGIN
    with Image.open(preview_png) as image:
        page_width, page_height = image.size
        while True:
            x0 = position["x"] / width_cm - margin
            y0 = position["y"] / height_cm - margin
            x1 = (position["x"] + position["width"]) / width_cm + margin
            y1 = (position["y"] + (position["height"] or 0)) / height_cm + margin
            at_slide_edge = [name for name, value in (("left", x0), ("top", y0)) if value < 0]
            at_slide_edge += [name for name, value in (("right", x1), ("bottom", y1)) if value > 1]
            box = (max(0, int(x0 * page_width)), max(0, int(y0 * page_height)),
                   min(page_width, int(x1 * page_width)), min(page_height, int(y1 * page_height)))
            if box[2] - box[0] < 16 or box[3] - box[1] < 16:
                return {"status": "failed", "reason": "the figure area is too small to crop"}
            crop = image.crop(box)
            touched = [side for side in _sides_touching_content(crop) if side not in at_slide_edge]
            attempts.append({"margin": round(margin, 3), "size_px": crop.size, "content_on_edges": touched})
            if not touched or margin >= MAX_MARGIN:
                break
            margin = round(min(margin + 0.02, MAX_MARGIN), 3)

        target.parent.mkdir(parents=True, exist_ok=True)
        crop.save(target)
        crop_size = crop.size

    if touched:
        status, reason = "clipped", f"drawing still reaches the crop edge on: {', '.join(touched)}"
    elif at_slide_edge:
        status, reason = "clipped", f"the object extends past the slide on: {', '.join(at_slide_edge)}"
    else:
        status, reason = "ok", None

    return {
        "status": status,
        "path": str(target),
        "sha256": sha256_file(target),
        "size_px": crop_size,
        "final_margin": round(margin, 3),
        "clipped_sides": touched or at_slide_edge,
        "attempts": attempts,
        "reason": reason,
        "source_object_path": obj["object_path"],
        "cropped_from": str(preview_png),
        "note": "cut from our own render, not a teacher export",
    }


def build_manifest(inventory_by_deck: dict, previews: dict, slide_sizes: dict, out_dir: Path) -> dict:
    """Everything a method may use, before any model is called."""
    from .supplement import embedded_table

    entries = []
    for deck, slide, tail, category, why in TARGETS:
        record = inventory_by_deck[deck]
        obj = find_object(record, slide, tail)
        identifier = target_id(deck, slide, tail)
        page = record["pages"][slide - 1]
        native_text = [
            line for o in page["objects"] if o["role"] == "shape"
            for line in o["text_sample"].splitlines() if line.strip()
        ]
        entry = {
            "target_id": identifier, "deck": deck, "source_slide_no": slide,
            "category": category, "why_chosen": why,
            "source_object_path": obj["object_path"] if obj else None,
            "source_object_kind": obj["kind"] if obj else None,
            "position_cm": obj.get("position_cm") if obj else None,
            "slide_title": page["title_candidate"],
            "native_text_lines": native_text,
            "page_preview": str(previews.get((deck, slide), "")),
        }
        if obj is None:
            entry["figure"] = {"status": "failed", "reason": "the object named in the suite is not on that slide"}
        else:
            entry["figure"] = crop_figure(
                previews[(deck, slide)], obj, slide_sizes[deck],
                out_dir / "figures" / f"{identifier}.png")
        entries.append(entry)

    protected = []
    for deck, slide in PROTECTED_CODE:
        page = inventory_by_deck[deck]["pages"][slide - 1]
        protected.append({
            "kind": "code", "deck": deck, "source_slide_no": slide,
            "lines": [line for o in page["objects"] if o["role"] == "shape"
                      for line in o["text_sample"].splitlines() if line.strip()],
        })
    for deck, slide in PROTECTED_TABLES:
        page = inventory_by_deck[deck]["pages"][slide - 1]
        obj = next((o for o in page["objects"] if o["kind"] == "embedded_spreadsheet"), None)
        table = embedded_table(Path("cours/odp_files") / deck, obj["href"]) if obj and obj.get("href") else None
        protected.append({
            "kind": "table", "deck": deck, "source_slide_no": slide,
            "source_object_path": obj["object_path"] if obj else None,
            "rows": table["rows"] if table else None,
        })

    return {
        "generated_at": utc_now(),
        "frozen": True,
        "note": ("Shared input for every method. Built from the ODP and our own render before any "
                 "model call. A figure marked failed or clipped is not used to judge a model."),
        "targets": entries,
        "protected": protected,
    }


# A second held-out set, from courseware uploaded after the first vision round
# was finished and scored. Nothing here was used to build or tune any method.
# Two courses: formal language theory (finite automata) and data structures
# (graph representations). The automata bring what the first round lacked:
# arrows whose direction carries meaning and symbols written on the edges.
HOLDOUT_V2 = [
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 13, "draw:frame[5]", "directed_labelled_edges",
     "directed graph, every edge carries a letter"),
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 14, "draw:frame[4]", "undirected_labelled_edges",
     "the same graph without arrows: says whether direction is invented"),
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 17, "draw:frame[3]", "directed_labelled_edges",
     "directed graph, recursive representation"),
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 6, "draw:frame[3]", "undirected_plain",
     "undirected graph, no edge labels"),
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 6, "draw:frame[4]", "directed_plain",
     "directed graph on the same slide, no edge labels"),
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 9, "draw:frame[3]", "undirected_plain",
     "set representation, undirected"),
    ("StructureDonnees_GrapheEtArbre_2025-2026.odp", 19, "draw:frame[3]", "directed_plain",
     "recursive representation of trees"),
    ("Theorie_Des_Langages_2024-2025.odp", 19, "draw:frame[3]", "duplicate_labels",
     "two finite automata side by side; the state names repeat, so only counts and direction are judged"),
]
