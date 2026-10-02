"""Behaviour of the stricter checks used for the MinerU comparison round.

The first round's checks were too easy to pass: any picture on the page counted
as the figure, and text counted as preserved when only its visible characters
matched. These tests hold the replacements to the harder rule.

Offline only: no database, no model, no network.
"""
import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from scripts.odp_m0 import checks_v2, fixtures, inventory, mineru_normalize, normalize
from scripts.odp_m0.mineru_probe import item_text


# --- text: characters, whitespace and symbols judged apart --------------------

def test_exact_text_passes():
    assert checks_v2.judge_text("SELECT\tnom,   prix", "SELECT\tnom,   prix")[0] == "correct"


def test_lost_tab_is_wrong_not_correct():
    status, detail = checks_v2.judge_text("\twhere cru = 'chablis'", "where cru = 'chablis'")
    assert status == "wrong"
    assert "tabs lost" in detail


def test_collapsed_spaces_are_wrong_not_correct():
    status, detail = checks_v2.judge_text("a   b", "a b")
    assert status == "wrong"
    assert "whitespace" in detail or "spaces" in detail


def test_changed_symbol_is_not_a_whitespace_problem():
    status, detail = checks_v2.judge_text("degre float);", "degre float),")
    assert status == "missing"


def test_absent_text_is_missing():
    assert checks_v2.judge_text("SET colonne1 = expr1", "something else entirely")[0] == "missing"


# --- inline groups: joining must not add or lose a character ------------------

def _docling_inline(parts):
    raw = {"body": {"children": [{"$ref": "#/groups/0"}]},
           "groups": [{"self_ref": "#/groups/0", "name": "slide-0", "label": "chapter",
                       "children": [{"$ref": "#/groups/1"}]},
                      {"self_ref": "#/groups/1", "name": "group", "label": "inline",
                       "children": [{"$ref": f"#/texts/{i}"} for i in range(len(parts))]}],
           "texts": [{"self_ref": f"#/texts/{i}", "label": "text", "text": part, "prov": [], "children": []}
                     for i, part in enumerate(parts)],
           "pictures": [], "tables": [], "pages": {}}
    return raw


def test_inline_join_restores_the_line_exactly(tmp_path):
    source = fixtures.write_minimal(tmp_path / "m.odp")
    record = inventory.inspect_file(source)
    parts = ["SET ", "colonne1 ", "= ", "expr1", ", ", "colonne2 ", "= ", "expr2", ", ..."]
    result = normalize.normalize_document(
        _docling_inline(parts), record, {"assets": []}, run_id="t", parser="d", parser_version="t",
        merge_inline=True)
    blocks = result["pages"][0]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["content"] == "SET colonne1 = expr1, colonne2 = expr2, ..."
    assert "\n" not in blocks[0]["content"]


def test_inline_join_keeps_tabs_and_double_spaces(tmp_path):
    source = fixtures.write_minimal(tmp_path / "m.odp")
    record = inventory.inspect_file(source)
    result = normalize.normalize_document(
        _docling_inline(["cru \t\t", "varchar(50)", "  not null,"]), record, {"assets": []},
        run_id="t", parser="d", parser_version="t", merge_inline=True)
    assert result["pages"][0]["blocks"][0]["content"] == "cru \t\tvarchar(50)  not null,"


def test_without_merging_the_same_input_stays_in_pieces(tmp_path):
    source = fixtures.write_minimal(tmp_path / "m.odp")
    record = inventory.inspect_file(source)
    result = normalize.normalize_document(
        _docling_inline(["SET ", "colonne1"]), record, {"assets": []},
        run_id="t", parser="d", parser_version="t", merge_inline=False)
    assert len(result["pages"][0]["blocks"]) == 2


# --- figures: another picture must not pass for the target object -------------

def _png(path: Path, size, colour=None, noisy=True):
    image = Image.new("RGB", size, colour or (255, 255, 255))
    if noisy:
        for x in range(0, size[0], 3):
            for y in range(0, size[1], 3):
                image.putpixel((x, y), ((x * 7) % 256, (y * 11) % 256, 90))
    image.save(path)
    return path


def _block(path: str, sha: str, size):
    return {"kind": "figure", "content": "", "assets": [
        {"path": path, "sha256": sha, "mime": "image/png", "width": size[0], "height": size[1],
         "decodable": True}]}


def test_a_picture_of_the_wrong_shape_does_not_pass(tmp_path):
    _png(tmp_path / "wide.png", (400, 100))
    source_object = {"position_cm": {"width": 10.0, "height": 9.0}}
    verdict = checks_v2.judge_figure(source_object, [_block("wide.png", "a", (400, 100))],
                                     asset_root=tmp_path, reference=None, duplicate_hashes=set())
    assert verdict.status == "wrong"
    assert "aspect ratio" in verdict.detail


def test_the_target_object_is_judged_not_whichever_picture_is_first(tmp_path):
    _png(tmp_path / "other.png", (400, 100))
    _png(tmp_path / "target.png", (330, 300))
    source_object = {"position_cm": {"width": 11.0, "height": 10.0}}
    blocks = [_block("other.png", "a", (400, 100)), _block("target.png", "b", (330, 300))]
    verdict = checks_v2.judge_figure(source_object, blocks, asset_root=tmp_path,
                                     reference=None, duplicate_hashes=set())
    assert verdict.status == "correct"
    assert verdict.evidence["asset"] == "target.png"


def test_one_filler_picture_reused_for_several_objects_never_passes(tmp_path):
    _png(tmp_path / "filler.png", (320, 180))
    source_object = {"position_cm": {"width": 16.0, "height": 9.0}}  # the shape even fits
    verdict = checks_v2.judge_figure(source_object, [_block("filler.png", "dup", (320, 180))],
                                     asset_root=tmp_path, reference=None, duplicate_hashes={"dup"})
    assert verdict.status == "wrong"
    assert "several different source objects" in verdict.detail


def test_a_blank_picture_does_not_pass(tmp_path):
    _png(tmp_path / "blank.png", (320, 180), colour=(255, 255, 255), noisy=False)
    source_object = {"position_cm": {"width": 16.0, "height": 9.0}}
    verdict = checks_v2.judge_figure(source_object, [_block("blank.png", "flat", (320, 180))],
                                     asset_root=tmp_path, reference=None, duplicate_hashes=set())
    assert verdict.status == "wrong"
    assert "no content" in verdict.detail


def test_a_missing_asset_file_is_reported(tmp_path):
    source_object = {"position_cm": {"width": 16.0, "height": 9.0}}
    verdict = checks_v2.judge_figure(source_object, [_block("gone.png", "x", (320, 180))],
                                     asset_root=tmp_path, reference=None, duplicate_hashes=set())
    assert verdict.status == "wrong"
    assert "not present" in verdict.detail


def test_no_figure_at_all_is_missing(tmp_path):
    verdict = checks_v2.judge_figure({"position_cm": {"width": 4.0, "height": 3.0}},
                                     [{"kind": "text", "content": "x", "assets": []}],
                                     asset_root=tmp_path, reference=None, duplicate_hashes=set())
    assert verdict.status == "missing"


def test_duplicate_hash_detection_finds_a_reused_picture():
    normalized = {"pages": [{"blocks": [
        {"kind": "figure", "assets": [{"sha256": "same"}]},
        {"kind": "figure", "assets": [{"sha256": "same"}]},
        {"kind": "figure", "assets": [{"sha256": "unique"}]},
    ]}]}
    assert checks_v2.duplicate_asset_hashes(normalized) == {"same"}


# --- tables: the cells must be where the source puts them ---------------------

def _table_odp(tmp_path):
    """An ODP whose slide holds an embedded spreadsheet with a known grid."""
    import zipfile
    from scripts.odp_m0.fixtures import CONTENT_HEADER, CONTENT_FOOTER, MANIFEST, STYLES, _slide
    body = ('  <draw:frame draw:name="t" svg:x="1cm" svg:y="5cm" svg:width="10cm" svg:height="5cm">'
            '<draw:object xlink:href="./Object 1"/></draw:frame>\n')
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:body><office:spreadsheet><table:table table:name="S">
  <table:table-row><table:table-cell><text:p>cru</text:p></table:table-cell>
   <table:table-cell><text:p>mill</text:p></table:table-cell></table:table-row>
  <table:table-row><table:table-cell><text:p>chablis</text:p></table:table-cell>
   <table:table-cell><text:p>1983</text:p></table:table-cell></table:table-row>
 </table:table></office:spreadsheet></office:body></office:document-content>
"""
    target = tmp_path / "table.odp"
    extra = (' <manifest:file-entry manifest:full-path="Object 1/" '
             'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>\n')
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=extra))
        archive.writestr("content.xml", CONTENT_HEADER + _slide("p1", "dp-visible", title="T", body_xml=body) + CONTENT_FOOTER)
        archive.writestr("styles.xml", STYLES)
        archive.writestr("Object 1/content.xml", sheet)
    return target


def test_table_with_cells_in_place_passes(tmp_path):
    odp = _table_odp(tmp_path)
    record = inventory.inspect_file(odp)
    obj = next(o for p in record["pages"] for o in p["objects"] if o["kind"] == "embedded_spreadsheet")
    blocks = [{"kind": "table", "content": "cru\tmill\nchablis\t1983", "assets": []}]
    status, detail, _ = checks_v2.judge_table(obj, blocks, odp)
    assert status == "correct"


def test_same_words_in_the_wrong_cells_fails(tmp_path):
    odp = _table_odp(tmp_path)
    record = inventory.inspect_file(odp)
    obj = next(o for p in record["pages"] for o in p["objects"] if o["kind"] == "embedded_spreadsheet")
    # Every value is present, but the columns are swapped.
    blocks = [{"kind": "table", "content": "mill\tcru\n1983\tchablis", "assets": []}]
    status, detail, evidence = checks_v2.judge_table(obj, blocks, odp)
    assert status == "wrong"
    assert evidence["misplaced_count"] == 4


def test_table_text_without_a_table_block_is_missing(tmp_path):
    odp = _table_odp(tmp_path)
    record = inventory.inspect_file(odp)
    obj = next(o for p in record["pages"] for o in p["objects"] if o["kind"] == "embedded_spreadsheet")
    blocks = [{"kind": "text", "content": "cru mill chablis 1983", "assets": []}]
    status, detail, evidence = checks_v2.judge_table(obj, blocks, odp)
    assert status == "missing"
    assert evidence["cells_as_loose_text"] == 4


# --- source binding -----------------------------------------------------------

def test_a_block_naming_the_wrong_slide_is_wrong():
    blocks = [{"kind": "text", "content": "x", "assets": [],
               "source_ref": {"source_slide_no": 9, "mapping_status": "mapped"}}]
    assert checks_v2.judge_source_binding(blocks, 3)[0] == "wrong"


def test_blocks_without_a_source_object_are_missing_not_correct():
    blocks = [{"kind": "text", "content": "x", "assets": [],
               "source_ref": {"source_slide_no": 3, "mapping_status": "unmapped"}}]
    status, detail = checks_v2.judge_source_binding(blocks, 3)
    assert status == "missing"
    assert "no source object" in detail


# --- MinerU output handling ---------------------------------------------------

def test_style_runs_join_without_a_separator():
    item = {"type": "text", "content": [
        {"type": "text", "content": "SET ", "styles": ["bold"]},
        {"type": "text", "content": "colonne1", "styles": ["italic"]}]}
    assert item_text(item) == "SET colonne1"


def test_nested_lists_become_one_block_each_with_their_depth(tmp_path):
    source = fixtures.write_minimal(tmp_path / "m.odp")
    record = inventory.inspect_file(source)
    raw = {"pages": [[
        {"type": "paragraph_title", "content": [{"type": "text", "content": "Slide 1"}]},
        {"type": "list", "content": [
            {"type": "text", "content": [{"type": "text", "content": "outer"}]},
            {"type": "list", "content": [
                {"type": "text", "content": [{"type": "text", "content": "inner"}]}]}]}]]}
    result = mineru_normalize.normalize_document(raw, record, {"assets": []}, run_id="t")
    blocks = result["pages"][0]["blocks"]
    assert [b["content"] for b in blocks] == ["Slide 1", "outer", "inner"]
    assert [b["list_depth"] for b in blocks] == [0, 1, 2]


def test_a_failed_mineru_probe_leaves_no_successful_artifact(tmp_path):
    from scripts.odp_m0 import mineru_probe

    source = fixtures.write_minimal(tmp_path / "m.odp")
    out = tmp_path / "out"
    out.mkdir()
    (out / "mineru_raw.json").write_text("{}", encoding="utf-8")
    result = mineru_probe.probe_file(source, out, python=shutil.which("false") or "/bin/false")
    assert result["status"] == "failed"
    assert not (out / "mineru_raw.json").exists()


def test_an_image_without_a_source_object_is_flagged_not_silently_accepted(tmp_path):
    source = fixtures.write_minimal(tmp_path / "m.odp")
    record = inventory.inspect_file(source)
    raw = {"pages": [[{"type": "image", "asset_path": "assets/p001-i000.png", "content": []}]]}
    assets = [{"page_no": 1, "item_index": 0, "path": "assets/p001-i000.png", "sha256": "a",
               "mime": "image/png", "width": 10, "height": 10, "decodable": True}]
    result = mineru_normalize.normalize_document(raw, record, {"assets": assets}, run_id="t")
    block = result["pages"][0]["blocks"][0]
    assert block["kind"] == "figure"
    assert block["source_ref"]["mapping_status"] == "unmapped"
    assert any(i["code"] == "source_identity_conflict" and i["blocks_release"] for i in block["issues"])
