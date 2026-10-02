"""Score one reading of a figure against the facts read out of the source file.

Every fact is judged on its own: a node, an edge, its weight, its direction, the
meaning of a colour. A fluent description earns nothing here.

Figures whose nodes carry no text can only be scored on structure, because the
model has no way to use the same names we do. Those are marked, and their
structure facts are counted apart from the labelled ones.
"""
from __future__ import annotations

import re

COLOUR_WORDS = {
    "#ff0000": {"red"}, "#ff3366": {"red", "pink", "crimson"}, "#00ff00": {"green"},
    "#008000": {"green"}, "#00a65d": {"green"}, "#99ccff": {"blue", "light blue", "lightblue", "cyan"},
    "default": {"blue", "light blue", "lightblue", "white", "cyan"},
}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _edge_key(a: str, b: str, directed: bool) -> tuple:
    return (a, b) if directed else tuple(sorted([a, b]))


def score_reading(facts: dict, answer: dict | None, *, labelled: bool,
                   countable: bool | None = None) -> dict:
    """Compare one model answer with the source facts, fact by fact.

    labelled  score node by node and edge by edge, using the node names.
    countable score how many nodes and edges there are. True by default when the
              nodes are named; a drawing whose nodes carry no text is not counted,
              because what counts as a node there is a matter of reading.
    """
    if countable is None:
        countable = labelled
    checks: list[dict] = []

    def add(check_id: str, category: str, verdict: str, expected=None, got=None, note=None):
        checks.append({"check_id": check_id, "category": category, "verdict": verdict,
                       "expected": expected, "got": got, "note": note})

    if answer is None:
        add("answer", "response", "no_answer", note="the model returned nothing usable")
        return _summary(checks, labelled)

    source_nodes = [n.get("name") or n.get("label") for n in facts["nodes"]]
    source_edges = facts["edge_facts"]
    # A node may be named by the value drawn in it or by the identifier label
    # printed beside it. Both are accepted; matching happens on the node id.
    name_to_id = {_norm(k): v for k, v in (facts.get("name_to_id") or {}).items()}
    directed_source = facts["directed_graph"]

    got_nodes = [_norm(n.get("name", "")) for n in answer.get("nodes", [])]
    got_edges = answer.get("edges", []) or []

    # 1. How many nodes. Only where the nodes carry text, so that "what counts as
    # a node" is not in doubt. In these drawings a small filled dot marks an empty
    # subtree: reading it as a node and reading it as a line ending are both fair,
    # so counting is recorded but not scored on unlabelled drawings.
    add("node_count", "structure",
        ("correct" if len(got_nodes) == len(source_nodes) else "wrong") if countable else "not_scored",
        expected=len(source_nodes), got=len(got_nodes),
        note=None if countable else "not scored: this drawing has no node text, so counting is ambiguous")

    # 2. Direction: an undirected drawing must not be reported with arrows.
    got_directed = any(bool(e.get("directed")) for e in got_edges)
    add("graph_direction", "direction",
        "correct" if got_directed == directed_source else "wrong",
        expected="directed" if directed_source else "undirected",
        got="directed" if got_directed else "undirected",
        note=None if got_directed == directed_source else
             ("arrows reported on a drawing that has none" if got_directed else "arrows in the drawing were not reported"))

    # 3. Edge count, for the same reason only where the nodes are named.
    add("edge_count", "structure",
        ("correct" if len(got_edges) == len(source_edges) else "wrong") if countable else "not_scored",
        expected=len(source_edges), got=len(got_edges),
        note=None if countable else "not scored: ambiguous on a drawing without node text")

    if labelled:
        got_ids = {name_to_id.get(name) for name in got_nodes if name_to_id.get(name)}
        for node in facts["nodes"]:
            node_id = node.get("node_id")
            names = sorted({x for x in ([node.get("label")] + (node.get("aliases") or [])) if x})
            add(f"node:{node_id}", "node", "correct" if node_id in got_ids else "missing",
                expected=" or ".join(names) or node_id)
        for extra in [n for n in got_nodes if n and not name_to_id.get(n)]:
            add(f"node_extra:{extra}", "node", "invented", got=extra,
                note="a node that is not in the drawing")

        source_map = {}
        for edge in source_edges:
            ids = edge.get("between_ids") or []
            if len(ids) != 2 or not all(ids):
                continue
            source_map[_edge_key(ids[0], ids[1], edge["directed"])] = edge
        matched = set()
        for edge in got_edges:
            a = name_to_id.get(_norm(edge.get("from", "")))
            b = name_to_id.get(_norm(edge.get("to", "")))
            if not a or not b:
                add(f"edge_extra:{_norm(edge.get('from',''))}-{_norm(edge.get('to',''))}", "edge",
                    "invented", got=f"{edge.get('from')}-{edge.get('to')}",
                    note="an edge between names that are not nodes in the drawing")
                continue
            # A directed edge only matches the same way round. Falling back to the
            # undirected key would let a reversed arrow pass as correct.
            key_directed = _edge_key(a, b, True)
            key_undirected = _edge_key(a, b, False)
            reversed_key = _edge_key(b, a, True)
            if key_directed in source_map and key_directed not in matched:
                source_edge, key = source_map[key_directed], key_directed
            elif (key_undirected in source_map and key_undirected not in matched
                  and not source_map[key_undirected]["directed"]):
                # An undirected line has no from and no to: naming its ends in
                # either order is the same line, not a reversed arrow.
                source_edge, key = source_map[key_undirected], key_undirected
            elif (reversed_key in source_map and reversed_key not in matched
                  and source_map[reversed_key]["directed"]):
                matched.add(reversed_key)
                names = source_map[reversed_key]["between"]
                add(f"edge:{'->'.join(names)}", "direction", "direction_wrong",
                    expected="->".join(names), got=f"{edge.get('from')}->{edge.get('to')}")
                continue
            else:
                add(f"edge_extra:{a}-{b}", "edge", "invented",
                    got=f"{edge.get('from')}-{edge.get('to')}", note="a relation that is not drawn")
                continue
            matched.add(key)
            add(f"edge:{'-'.join(source_edge['between'])}", "edge", "correct",
                expected="-".join(source_edge["between"]))
            expected_weight = _norm(source_edge.get("weight_label") or "")
            got_weight = _norm(edge.get("weight") or "")
            if expected_weight:
                add(f"weight:{'-'.join(source_edge['between'])}", "weight",
                    "correct" if got_weight == expected_weight else
                    ("missing" if not got_weight else "wrong"),
                    expected=expected_weight, got=got_weight)
            elif got_weight:
                add(f"weight_extra:{'-'.join(source_edge['between'])}", "weight", "invented", got=got_weight,
                    note="a weight on a line that has none")
        for key, edge in source_map.items():
            if key not in matched:
                add(f"edge_missing:{'-'.join(key)}", "edge", "missing",
                    expected="-".join(edge["between"]))

    # 4. Colour meaning: only a legend in the image can justify it.
    meaning = _norm(answer.get("colour_meaning", ""))
    says_not_stated = any(word in meaning for word in ("not stated", "no legend", "not specified", "unknown", "not given"))
    add("colour_meaning", "colour",
        "correct" if says_not_stated else "unsupported_claim",
        expected="not stated in the image (these drawings carry no legend)", got=meaning[:120],
        note=None if says_not_stated else "a meaning was asserted although the drawing has no legend")

    # 5. Colours of the nodes, when they are named.
    if labelled:
        for node in facts["nodes"]:
            node_id = node.get("node_id")
            names = {_norm(x) for x in ([node.get("label")] + (node.get("aliases") or [])) if x}
            if not names:
                continue
            name = node.get("name") or node.get("label")
            expected_colour = node.get("fill_color") or "default"
            accepted = COLOUR_WORDS.get(expected_colour, set())
            got = next((_norm(n.get("colour", "")) for n in answer.get("nodes", [])
                        if _norm(n.get("name", "")) in names), "")
            ok = any(word in got for word in accepted) if accepted else bool(got)
            add(f"colour:{node_id}", "colour", "correct" if ok else "wrong",
                expected=f"{expected_colour} ({'/'.join(sorted(accepted))})", got=got)

    return _summary(checks, labelled)


def _summary(checks: list[dict], labelled: bool) -> dict:
    counts: dict[str, int] = {}
    by_category: dict[str, dict] = {}
    for check in checks:
        counts[check["verdict"]] = counts.get(check["verdict"], 0) + 1
        by_category.setdefault(check["category"], {}).setdefault(check["verdict"], 0)
        by_category[check["category"]][check["verdict"]] += 1
    invented = counts.get("invented", 0) + counts.get("unsupported_claim", 0)
    scored_total = len(checks) - counts.get("not_scored", 0)
    return {
        "labelled_figure": labelled,
        "checks": checks,
        "counts": counts,
        "by_category": by_category,
        "correct": counts.get("correct", 0),
        "missing": counts.get("missing", 0),
        "wrong": counts.get("wrong", 0) + counts.get("direction_wrong", 0),
        "invented": invented,
        "not_scored": counts.get("not_scored", 0),
        "total": scored_total,
    }


def flagged_uncertainty(answer: dict | None, scored: dict) -> dict:
    """Did the model say it was unsure, when it was in fact wrong?"""
    if answer is None:
        return {"said_unsure": False, "had_errors": True, "silent_errors": True}
    errors = scored["missing"] + scored["wrong"] + scored["invented"]
    said = bool(answer.get("cannot_confirm")) or bool(answer.get("needs_bigger_picture"))
    return {"said_unsure": said, "had_errors": errors > 0, "silent_errors": errors > 0 and not said,
            "error_count": errors}
