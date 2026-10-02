"""Behaviour of the offline M0 ODP tools. No database, no model, no network.

The real courseware has no hidden slide, no notes text and no formula object, so
those rules are checked against fixtures we build here. A fixture passing is a
protocol result, never a quality result on real material.
"""
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from scripts.odp_m0 import evaluate, fixtures, inventory, normalize, render, supplement
from scripts.odp_m0.common import M0Error, ZipLimits, new_run_dir, open_odp, parse_xml, sha256_json


@pytest.fixture
def sample(tmp_path):
    return fixtures.write_hidden_notes_formula(tmp_path / "sample.odp")


def test_hidden_slide_found_through_the_style_chain(sample):
    record = inventory.inspect_file(sample)
    hidden = [page for page in record["pages"] if page["visibility"] == "hidden"]
    assert [page["source_slide_no"] for page in hidden] == [2]
    assert "dp-hidden-child -> dp-hidden-parent" in hidden[0]["visibility_basis"]


def test_visibility_is_not_decided_by_a_string_search(sample):
    # The word "hidden" appears in a style name on every slide of this file.
    record = inventory.inspect_file(sample)
    assert record["visible_slide_count"] == 2
    assert record["hidden_slide_count"] == 1


def test_explicit_whitespace_survives_exactly(sample):
    record = inventory.inspect_file(sample)
    code = next(o for o in record["pages"][0]["objects"] if o["draw_name"] == "code")
    assert "SELECT\tnom,   prix" in code["text_sample"]
    assert "FROM\tproduit\nWHERE prix > 10;" in code["text_sample"]


def test_notes_are_recorded_but_stay_out_of_the_student_view(sample, tmp_path):
    record = inventory.inspect_file(sample)
    assert record["pages"][0]["notes_non_empty"] is True
    cleaned = render.make_visible_only_copy(sample, tmp_path / "clean.odp")
    assert cleaned["removed_hidden_slides"] == [2]
    assert cleaned["removed_notes_nodes"] == 1
    cleaned_record = inventory.inspect_file(tmp_path / "clean.odp")
    student_text = "\n".join(
        obj["text_sample"] for page in cleaned_record["pages"] for obj in page["objects"]
    )
    assert "Notes secret" not in student_text
    assert "hidden slide body text" not in student_text


def test_the_original_file_is_never_modified(sample, tmp_path):
    before = inventory.inspect_file(sample)["sha256"]
    render.make_visible_only_copy(sample, tmp_path / "clean.odp")
    assert inventory.inspect_file(sample)["sha256"] == before


def test_duplicate_titles_do_not_become_an_identity(sample):
    record = inventory.inspect_file(sample)
    titles = [page["title_candidate"] for page in record["pages"]]
    assert titles[0] == titles[2] == "Same title"
    paths = [
        obj["object_path"]
        for page in record["pages"] for obj in page["objects"] if obj["role"] == "shape"
    ]
    assert len(paths) == len(set(paths))


def test_source_identity_is_stable_across_repeated_runs(sample):
    first = inventory.inspect_file(sample)
    second = inventory.inspect_file(sample)
    assert sha256_json(first["pages"]) == sha256_json(second["pages"])


def test_formula_object_is_kept_as_its_own_original(sample, tmp_path):
    record = inventory.inspect_file(sample)
    formula = next(
        obj for page in record["pages"] for obj in page["objects"] if obj["kind"] == "embedded_formula"
    )
    saved = supplement.embedded_formula(sample, formula["href"], tmp_path / "formula.xml")
    assert saved["asset"]["mime"] == "application/mathml+xml"
    assert "math:math" in (tmp_path / "formula.xml").read_text(encoding="utf-8")


def test_an_unclassified_shape_is_reported_not_dropped(tmp_path):
    source = fixtures.write_minimal(tmp_path / "odd.odp")
    with zipfile.ZipFile(source) as archive:
        content = archive.read("content.xml").decode("utf-8")
        others = {name: archive.read(name) for name in archive.namelist() if name != "content.xml"}
    content = content.replace(
        "</draw:page>", '<draw:made-up-shape draw:name="x"><text:p>strange</text:p></draw:made-up-shape></draw:page>'
    )
    target = tmp_path / "odd2.odp"
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in others.items():
            archive.writestr(name, data)
        archive.writestr("content.xml", content)
    record = inventory.inspect_file(target)
    unknown = [o for page in record["pages"] for o in page["objects"] if o["kind"] == "unknown"]
    assert record["unknown_object_count"] == 1
    assert unknown[0]["notes"] == ["unclassified tag draw:made-up-shape"]


def test_external_reference_is_listed(tmp_path):
    source = fixtures.write_external_reference(tmp_path / "ext.odp")
    record = inventory.inspect_file(source)
    assert record["external_references"] == ["https://example.invalid/remote.png"]


def test_doctype_declaration_is_refused(tmp_path):
    source = fixtures.write_doctype_bomb(tmp_path / "doctype.odp")
    record = inventory.inspect_file(source)
    assert record["status"] == "failed"
    assert "doctype" in record["reason"]


def test_entry_outside_the_archive_root_is_refused(tmp_path):
    source = fixtures.write_path_escape(tmp_path / "escape.odp")
    record = inventory.inspect_file(source)
    assert record["status"] == "failed"
    assert "escapes the archive root" in record["reason"]


def test_a_broken_zip_gives_a_reason_not_an_empty_result(tmp_path):
    broken = tmp_path / "broken.odp"
    broken.write_bytes(b"this is not a zip file")
    record = inventory.inspect_file(broken)
    assert record["status"] == "failed"
    assert "not a readable ZIP" in record["reason"]
    assert "source_slide_count" not in record


def test_size_limit_is_reported_with_its_reason(tmp_path):
    source = fixtures.write_minimal(tmp_path / "small.odp")
    with pytest.raises(M0Error) as error:
        open_odp(source, ZipLimits(max_archive_bytes=10))
    assert "over the" in str(error.value)


def test_oversized_entry_is_recorded_rather_than_read(tmp_path):
    source = fixtures.write_minimal(tmp_path / "small.odp")
    with open_odp(source, ZipLimits(max_entry_bytes=200)) as archive:
        assert archive.limit_hits
        assert all(hit["reason"] == "entry_over_limit" for hit in archive.limit_hits)


def test_xml_entity_declaration_is_refused():
    with pytest.raises(M0Error):
        parse_xml(b'<?xml version="1.0"?><!DOCTYPE a [ <!ENTITY b "c"> ]><a/>')


def _docling_like(pages):
    """A Docling-shaped result: one slide group per slide, in document order."""
    raw = {"body": {"children": []}, "groups": [], "texts": [], "pictures": [], "tables": [], "pages": {}}
    for index, texts in enumerate(pages):
        group_ref = f"#/groups/{len(raw['groups'])}"
        group = {"self_ref": group_ref, "name": f"slide-{index}", "label": "chapter", "children": []}
        raw["groups"].append(group)
        raw["body"]["children"].append({"$ref": group_ref})
        for label, text in texts:
            ref = f"#/texts/{len(raw['texts'])}"
            raw["texts"].append({"self_ref": ref, "label": label, "text": text, "prov": [], "children": []})
            group["children"].append({"$ref": ref})
    return raw


def test_page_numbers_follow_the_source_order_not_the_extraction_order(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([
        [("title", "Same title"), ("text", "SELECT\tnom,   prix")],
        [("title", "Hidden slide"), ("text", "hidden slide body text")],
        [("title", "Same title"), ("text", "repeated paragraph text")],
    ])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    assert [page["source_slide_no"] for page in result["pages"]] == [1, 2, 3]
    assert [page["visible_page_no"] for page in result["pages"]] == [1, None, 2]
    assert result["pages"][1]["visibility"] == "hidden"


def test_repeated_text_is_ambiguous_rather_than_matched(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([
        [("text", "repeated paragraph text")],
        [("text", "hidden slide body text")],
        [("text", "repeated paragraph text")],
    ])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    # Slide 3 carries the same paragraph in two frames, so the source object
    # cannot be decided and must not be guessed.
    third = result["pages"][2]["blocks"][0]["source_ref"]
    assert third["mapping_status"] == "ambiguous"
    assert third["xml_object_path"] is None
    assert third["candidate_count"] == 2
    # The same text on another slide is not ambiguous: the slide tells them apart.
    first = result["pages"][0]["blocks"][0]["source_ref"]
    assert first["mapping_status"] == "mapped"


def test_text_docling_invented_stays_unmapped(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([[("text", "a line that is in no source object")], [], []])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    block = result["pages"][0]["blocks"][0]
    assert block["source_ref"]["mapping_status"] == "unmapped"
    assert block["completeness"]["extraction_status"] == "unknown"


def test_missing_bbox_stays_null(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([[("text", "repeated paragraph text")], [], []])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    assert result["totals"]["blocks_with_bbox"] == 0
    assert result["pages"][0]["blocks"][0]["source_ref"]["bbox"] is None


def test_a_failed_probe_leaves_no_successful_artifact(tmp_path):
    from scripts.odp_m0 import docling_probe

    source = fixtures.write_minimal(tmp_path / "min.odp")
    out = tmp_path / "out"
    out.mkdir()
    (out / "docling_raw.json").write_text("{}", encoding="utf-8")
    result = docling_probe.probe_file(source, out, python=shutil.which("false") or "/bin/false")
    assert result["status"] == "failed"
    assert not (out / "docling_raw.json").exists()


def test_an_existing_run_directory_is_never_reused(tmp_path):
    new_run_dir("run-1", tmp_path)
    with pytest.raises(M0Error):
        new_run_dir("run-1", tmp_path)


def _evaluation(results_marker="a"):
    return {
        "run_id": "r1",
        "checks_sha256": "checks",
        "results_sha256": results_marker,
        "artifact_hashes": {"deck.odp": {"normalized_sha256": "n1"}},
    }


def test_a_review_stops_counting_when_the_artifacts_change():
    evaluation = _evaluation()
    review = evaluate.bind_review(evaluation, reviewer="tester", reviewer_kind="model", findings=[])
    assert evaluate.review_is_valid(review, evaluation)[0] is True
    changed = _evaluation(results_marker="b")
    valid, reason = evaluate.review_is_valid(review, changed)
    assert valid is False
    assert "results changed" in reason


def test_an_automated_reviewer_cannot_sign_as_something_else():
    with pytest.raises(ValueError):
        evaluate.bind_review(_evaluation(), reviewer="agent", reviewer_kind="teacher", findings=[])


def test_automatic_checks_report_only_automated_status(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([[("text", "SELECT nom, prix")], [], []])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    check = {
        "check_id": "c1", "deck": "sample.odp", "source_slide_no": 1, "category": "code_sql",
        "importance": "high", "verification": "automatic", "assertion": "exact_text_present",
        "expected": "SELECT\tnom,   prix",
    }
    outcome = evaluate.run_check(check, result, record)
    # The text is there but the tab and the three spaces are gone: that is wrong, not correct.
    assert outcome["status"] == "wrong"
    assert outcome["reviewer_kind"] == "automated"


def test_a_missing_figure_is_missing_not_unknown(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([[("text", "repeated paragraph text")], [], []])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    check = {
        "check_id": "c2", "deck": "sample.odp", "source_slide_no": 1, "category": "figure_embedded_object",
        "importance": "high", "verification": "automatic", "assertion": "figure_block_with_asset",
        "expected": {}, "source_object_path": None,
    }
    assert evaluate.run_check(check, result, record)["status"] == "missing"


def test_human_checks_are_left_pending_not_answered(sample):
    record = inventory.inspect_file(sample)
    raw = _docling_like([[("text", "repeated paragraph text")], [], []])
    result = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    check_set = {
        "checks_sha256": "checks",
        "checks": [{
            "check_id": "h1", "deck": "sample.odp", "source_slide_no": 1, "category": "reading_order",
            "importance": "high", "verification": "human", "assertion": "blocks follow the reading order",
        }],
    }
    evaluation = evaluate.evaluate_run(
        check_set, {"sample.odp": result}, {"sample.odp": record}, run_id="r", artifact_hashes={}
    )
    assert evaluation["pending_human_checks"][0]["status"] == "awaiting_human_review"
    assert evaluation["human_review_state"] == "awaiting_human_review"
    assert evaluation["automatic_results"] == []


def test_the_supplement_does_not_modify_the_baseline(sample, tmp_path):
    record = inventory.inspect_file(sample)
    raw = _docling_like([[("text", "repeated paragraph text")], [], []])
    baseline = normalize.normalize_document(
        raw, record, {"assets": []}, run_id="test", parser="docling-odp-native", parser_version="test"
    )
    before = json.dumps(baseline, sort_keys=True)
    variant = supplement.build_supplement(
        baseline, record, sample, preview_images=[], asset_dir=tmp_path / "assets"
    )
    assert json.dumps(baseline, sort_keys=True) == before
    assert variant["variant"] == "docling+native-supplement"
    assert variant["supplement_added"]["formula_blocks"] == 1
    formula = next(b for p in variant["pages"] for b in p["blocks"] if b["kind"] == "formula")
    assert formula["format"] == "plain"
    assert formula["assets"][0]["mime"] == "application/mathml+xml"
    assert formula["completeness"]["extraction_status"] == "partial"


# --- Integration checks -------------------------------------------------------
# These need the real LibreOffice image and the real M0 Docling environment, so
# they are off by default. A skip is a skip: it is never counted as a pass.

INTEGRATION = os.environ.get("ODP_M0_INTEGRATION") == "1"
integration = pytest.mark.skipif(
    not INTEGRATION,
    reason="set ODP_M0_INTEGRATION=1 with the render image built and the M0 python available",
)


@integration
def test_real_render_keeps_hidden_slides_and_notes_out_of_the_preview(sample, tmp_path):
    result = render.render_file(sample, tmp_path / "render")
    assert result.status == "ok", result.reason
    assert result.pdf_page_count == 2, "the hidden slide must not be in the preview"
    text = "".join(render.pdf_page_text(Path(result.pdf_path), page) for page in (1, 2))
    assert "Notes secret" not in text
    assert "hidden slide body text" not in text


@integration
def test_real_docling_conversion_produces_one_group_per_slide(sample, tmp_path):
    from scripts.odp_m0 import docling_probe

    python = os.environ.get("ODP_M0_PYTHON", "/tmp/odp-m0-env/bin/python")
    result = docling_probe.probe_file(sample, tmp_path / "probe", python=python)
    assert result["status"] == "ok", result.get("reason")
    raw = json.loads((tmp_path / "probe" / "docling_raw.json").read_text(encoding="utf-8"))
    slide_groups = [g for g in raw["groups"] if (g.get("name") or "").startswith("slide-")]
    assert len(slide_groups) == 3


@integration
def test_default_export_would_leak_a_hidden_slide(sample, tmp_path):
    """Why the pipeline renders a cleaned copy rather than the file as it stands.

    LibreOffice's own PDF export keeps hidden slides, with their text. Notes are
    left out by default, but hidden slides are not.
    """
    default = render.render_file(sample, tmp_path / "default", hidden_slides=[])
    assert default.status == "ok", default.reason
    assert default.pdf_page_count == 3
    text = "".join(render.pdf_page_text(Path(default.pdf_path), page) for page in (1, 2, 3))
    assert "hidden slide body text" in text
    assert "Notes secret" not in text
