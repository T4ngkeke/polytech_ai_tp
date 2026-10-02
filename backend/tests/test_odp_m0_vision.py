"""Behaviour of the vision-benefit round: figure input, source facts, scoring.

The point of these tests is that a wrong input or a shaky fact list must never
be charged to the model. Offline: no model call, no network, no database.
"""
import json
import zipfile

import pytest
from PIL import Image

from scripts.odp_m0 import checks_v2, figure_facts, figure_render, fixtures, inventory, vision_score


# --- building a single object copy -------------------------------------------

def _two_object_odp(tmp_path):
    from scripts.odp_m0.fixtures import CONTENT_HEADER, CONTENT_FOOTER, MANIFEST, STYLES, _slide
    body = (
        '  <draw:frame draw:name="keep" svg:x="2cm" svg:y="5cm" svg:width="8cm" svg:height="6cm">'
        "<draw:text-box><text:p>target figure</text:p></draw:text-box></draw:frame>\n"
        '  <draw:frame draw:name="other" svg:x="12cm" svg:y="5cm" svg:width="8cm" svg:height="6cm">'
        "<draw:text-box><text:p>neighbour text</text:p></draw:text-box></draw:frame>\n"
    )
    target = tmp_path / "two.odp"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=""))
        archive.writestr("content.xml", CONTENT_HEADER
                         + _slide("p1", "dp-visible", title="Slide one", body_xml=body, notes="secret note")
                         + _slide("p2", "dp-visible", title="Slide two")
                         + CONTENT_FOOTER)
        archive.writestr("styles.xml", STYLES)
    return target


def test_single_object_copy_keeps_only_the_target(tmp_path):
    source = _two_object_odp(tmp_path)
    path = "/office:document-content/office:body/office:presentation/draw:page[1]/draw:frame[2]"
    info = figure_render.write_single_object_copy(source, 1, path, tmp_path / "single.odp")
    record = inventory.inspect_file(tmp_path / "single.odp")
    assert record["source_slide_count"] == 1
    texts = [o["text_sample"] for p in record["pages"] for o in p["objects"] if o["role"] == "shape"]
    assert any("target figure" in t for t in texts)
    assert not any("neighbour text" in t for t in texts)
    assert info["removed_siblings"] >= 1


def test_single_object_copy_drops_notes_and_leaves_the_original_alone(tmp_path):
    source = _two_object_odp(tmp_path)
    before = inventory.inspect_file(source)["sha256"]
    path = "/office:document-content/office:body/office:presentation/draw:page[1]/draw:frame[1]"
    figure_render.write_single_object_copy(source, 1, path, tmp_path / "single.odp")
    assert inventory.inspect_file(source)["sha256"] == before
    record = inventory.inspect_file(tmp_path / "single.odp")
    assert record["pages"][0]["notes_non_empty"] is False


def test_trim_reports_a_drawing_that_reaches_the_page_edge(tmp_path):
    image = Image.new("RGB", (60, 60), (255, 255, 255))
    for x in range(20, 40):
        image.putpixel((x, 0), (0, 0, 0))  # a stroke running into the top edge
    trimmed, at_edge = figure_render._trim(image)
    assert "top" in at_edge
    assert trimmed is not None


def test_trim_says_nothing_when_the_drawing_sits_clear_of_the_edge(tmp_path):
    image = Image.new("RGB", (60, 60), (255, 255, 255))
    for x in range(20, 40):
        for y in range(20, 40):
            image.putpixel((x, y), (10, 10, 10))
    trimmed, at_edge = figure_render._trim(image)
    assert at_edge == []
    assert trimmed.size[0] < 60


# --- facts read out of the source drawing ------------------------------------

def _drawing_odp(tmp_path, *, arrow_on_start=False, with_label=True):
    """A slide holding an embedded drawing: two nodes and one connector."""
    from scripts.odp_m0.fixtures import CONTENT_HEADER, CONTENT_FOOTER, MANIFEST, STYLES, _slide
    marker = ('draw:marker-start="Arrow"' if arrow_on_start else 'draw:marker-end="Arrow"')
    label = '<draw:frame svg:x="1.2cm" svg:y="0.6cm" svg:width="1cm" svg:height="0.5cm">' \
            '<draw:text-box><text:p>nodeA</text:p></draw:text-box></draw:frame>' if with_label else ""
    drawing = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:automatic-styles>
  <style:style style:name="gnode" style:family="graphic">
   <style:graphic-properties draw:fill-color="#ff0000"/></style:style>
  <style:style style:name="gedge" style:family="graphic">
   <style:graphic-properties {marker}/></style:style>
 </office:automatic-styles>
 <office:body><office:drawing><draw:page draw:name="d">
  <draw:custom-shape draw:style-name="gnode" svg:x="1cm" svg:y="1cm" svg:width="1cm" svg:height="1cm">
   <text:p>A</text:p></draw:custom-shape>
  <draw:custom-shape svg:x="5cm" svg:y="1cm" svg:width="1cm" svg:height="1cm">
   <text:p>B</text:p></draw:custom-shape>
  <draw:connector draw:style-name="gedge" svg:x1="2cm" svg:y1="1.5cm" svg:x2="5cm" svg:y2="1.5cm">
   <text:p>7</text:p></draw:connector>
  {label}
 </draw:page></office:drawing></office:body></office:document-content>
"""
    body = ('  <draw:frame draw:name="fig" svg:x="1cm" svg:y="4cm" svg:width="10cm" svg:height="6cm">'
            '<draw:object xlink:href="./Object 1"/></draw:frame>\n')
    target = tmp_path / f"draw-{arrow_on_start}-{with_label}.odp"
    extra = (' <manifest:file-entry manifest:full-path="Object 1/" '
             'manifest:media-type="application/vnd.oasis.opendocument.graphics"/>\n')
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=extra))
        archive.writestr("content.xml", CONTENT_HEADER
                         + _slide("p1", "dp-visible", title="Figure", body_xml=body) + CONTENT_FOOTER)
        archive.writestr("styles.xml", STYLES)
        archive.writestr("Object 1/content.xml", drawing)
    return target


FIG_PATH = "/office:document-content/office:body/office:presentation/draw:page[1]/draw:frame[2]"


def test_facts_read_nodes_edges_weight_and_colour(tmp_path):
    facts = figure_facts.facts_for(_drawing_odp(tmp_path), 1, FIG_PATH)
    assert facts["status"] == "ok"
    assert facts["node_labels"] == ["A", "B"]
    assert facts["colour_groups"]["#ff0000"] == ["A"]
    edge = facts["edge_facts"][0]
    assert edge["between"] == ["A", "B"]
    assert edge["weight_label"] == "7"
    assert edge["directed"] is True


def test_an_arrow_on_the_first_point_reverses_the_reading_direction(tmp_path):
    facts = figure_facts.facts_for(_drawing_odp(tmp_path, arrow_on_start=True), 1, FIG_PATH)
    edge = facts["edge_facts"][0]
    assert edge["between"] == ["B", "A"]
    # The ids must follow the names, or a later comparison silently flips.
    ids = {n["node_id"]: (n.get("name") or n["label"]) for n in facts["nodes"]}
    assert [ids[i] for i in edge["between_ids"]] == ["B", "A"]


def test_an_identifier_label_beside_a_node_becomes_another_name_for_it(tmp_path):
    facts = figure_facts.facts_for(_drawing_odp(tmp_path), 1, FIG_PATH)
    names = {entry["primary"]: entry["accepted_names"] for entry in facts["names_by_node"]}
    assert "nodeA" in names["A"]
    assert facts["name_to_id"]["nodea"] == facts["name_to_id"]["a"]


def test_the_drawing_reports_no_legend(tmp_path):
    facts = figure_facts.facts_for(_drawing_odp(tmp_path), 1, FIG_PATH)
    assert facts["legend_present"] is False
    assert "no legend" in facts["colour_meaning"]


# --- scoring one reading ------------------------------------------------------

def _facts(tmp_path):
    return figure_facts.facts_for(_drawing_odp(tmp_path), 1, FIG_PATH)


def _answer(**over):
    base = {
        "nodes": [{"name": "A", "colour": "red"}, {"name": "B", "colour": "light blue"}],
        "edges": [{"from": "A", "to": "B", "directed": True, "weight": "7"}],
        "colour_meaning": "not stated in the image",
        "cannot_confirm": [], "needs_bigger_picture": False,
    }
    base.update(over)
    return base


def test_a_correct_reading_scores_everything(tmp_path):
    scored = vision_score.score_reading(_facts(tmp_path), _answer(), labelled=True)
    assert scored["missing"] == 0 and scored["wrong"] == 0 and scored["invented"] == 0
    assert scored["correct"] == scored["total"]


def test_the_other_name_for_a_node_is_accepted(tmp_path):
    answer = _answer(nodes=[{"name": "nodeA", "colour": "red"}, {"name": "B", "colour": "blue"}],
                      edges=[{"from": "nodeA", "to": "B", "directed": True, "weight": "7"}])
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    assert scored["missing"] == 0 and scored["invented"] == 0


def test_a_reversed_arrow_is_caught(tmp_path):
    answer = _answer(edges=[{"from": "B", "to": "A", "directed": True, "weight": "7"}])
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    assert any(c["verdict"] == "direction_wrong" for c in scored["checks"])
    assert scored["wrong"] >= 1


def test_calling_an_undirected_line_an_arrow_is_caught(tmp_path):
    facts = _facts(tmp_path)
    for edge in facts["edges"]:
        edge["directed"] = False
    facts["directed_graph"] = False
    for edge in facts["edge_facts"]:
        edge["directed"] = False
    answer = _answer(edges=[{"from": "A", "to": "B", "directed": True, "weight": "7"}])
    scored = vision_score.score_reading(facts, answer, labelled=True)
    direction = next(c for c in scored["checks"] if c["check_id"] == "graph_direction")
    assert direction["verdict"] == "wrong"
    assert "has none" in direction["note"]


def test_a_relation_that_is_not_drawn_counts_as_invented(tmp_path):
    answer = _answer(edges=[{"from": "A", "to": "B", "directed": True, "weight": "7"},
                             {"from": "B", "to": "A", "directed": True, "weight": "9"}])
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    assert scored["invented"] >= 1


def test_a_wrong_weight_is_not_counted_as_correct(tmp_path):
    answer = _answer(edges=[{"from": "A", "to": "B", "directed": True, "weight": "4"}])
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    weight = next(c for c in scored["checks"] if c["check_id"].startswith("weight:"))
    assert weight["verdict"] == "wrong"


def test_claiming_a_colour_meaning_without_a_legend_is_an_unsupported_claim(tmp_path):
    answer = _answer(colour_meaning="red marks the node already visited")
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    meaning = next(c for c in scored["checks"] if c["check_id"] == "colour_meaning")
    assert meaning["verdict"] == "unsupported_claim"
    assert scored["invented"] >= 1


def test_counting_is_not_scored_when_the_nodes_have_no_text(tmp_path):
    scored = vision_score.score_reading(_facts(tmp_path), _answer(), labelled=False)
    counts = [c for c in scored["checks"] if c["check_id"] in ("node_count", "edge_count")]
    assert all(c["verdict"] == "not_scored" for c in counts)
    assert scored["not_scored"] == 2


def test_no_answer_is_not_silently_a_pass(tmp_path):
    scored = vision_score.score_reading(_facts(tmp_path), None, labelled=True)
    assert scored["correct"] == 0
    assert scored["checks"][0]["verdict"] == "no_answer"


def test_errors_without_a_word_of_doubt_are_flagged(tmp_path):
    answer = _answer(edges=[{"from": "B", "to": "A", "directed": True, "weight": "7"}], cannot_confirm=[])
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    flag = vision_score.flagged_uncertainty(answer, scored)
    assert flag["silent_errors"] is True


def test_saying_it_is_unsure_is_recorded(tmp_path):
    answer = _answer(edges=[{"from": "B", "to": "A", "directed": True, "weight": "7"}],
                      cannot_confirm=["the arrow head is hard to see"])
    scored = vision_score.score_reading(_facts(tmp_path), answer, labelled=True)
    flag = vision_score.flagged_uncertainty(answer, scored)
    assert flag["said_unsure"] is True and flag["silent_errors"] is False


def test_method_a_claims_no_relation(tmp_path):
    from scripts.odp_m0 import vision_run
    target = {"figure": {"path": "x.png"}, "facts": {"node_labels": ["A", "B"]},
              "native_text_lines": ["some text"], "slide_title": "Figure"}
    result = vision_run.method_a(target)
    assert result["claims_relations"] is False
    assert result["native_labels"] == ["A", "B"]


def test_an_undirected_line_named_the_other_way_round_is_still_correct(tmp_path):
    """An undirected line has no from and no to.

    Naming its ends in the other order is the same line. Calling that a reversed
    arrow marks a correct reading as wrong, which is what happened before.
    """
    facts = _facts(tmp_path)
    facts["directed_graph"] = False
    for edge in facts["edges"]:
        edge["directed"] = False
    for edge in facts["edge_facts"]:
        edge["directed"] = False
        edge["between"] = sorted(edge["between"])
        edge["between_ids"] = sorted(edge["between_ids"])
    answer = _answer(edges=[{"from": "B", "to": "A", "directed": False, "weight": "7"}])
    scored = vision_score.score_reading(facts, answer, labelled=True)
    assert not any(c["verdict"] == "direction_wrong" for c in scored["checks"])
    assert scored["wrong"] == 0 and scored["invented"] == 0
