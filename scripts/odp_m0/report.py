"""Statistics and static comparison pages. No model is called here.

A comparison page shows, for one slide: our own rendered preview, the source
objects and their expected text, what the parser produced, and what is missing
or wrong. Automatic checks are labelled "automated" on the page, because that is
all they can establish.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .common import utc_now

STYLE = """
body { font-family: system-ui, sans-serif; margin: 0 auto; max-width: 1200px; padding: 16px; }
h1, h2 { line-height: 1.25; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; vertical-align: top; font-size: 14px; }
th { background: #f2f2f2; }
pre { background: #f7f7f7; padding: 8px; overflow-x: auto; white-space: pre-wrap; font-size: 13px; }
img { max-width: 100%; border: 1px solid #ddd; }
.correct { color: #14622a; } .missing { color: #a11; } .wrong { color: #a55b00; } .unknown { color: #555; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
.tag { display: inline-block; background: #eee; border-radius: 3px; padding: 1px 6px; margin-right: 4px; font-size: 12px; }
"""


def _escape(value) -> str:
    return html.escape(str(value if value is not None else ""))


def comparison_page(suite_page: dict, inventory_file: dict, normalized: dict, results: list[dict], preview: dict | None) -> str:
    slide_no = suite_page["source_slide_no"]
    source_page = inventory_file["pages"][slide_no - 1]
    page = next((p for p in normalized["pages"] if p["source_slide_no"] == slide_no), None)

    source_rows = "".join(
        f"<tr><td>{_escape(obj['kind'])}</td><td><code>{_escape(obj['object_path'].split('/')[-1])}</code></td>"
        f"<td><pre>{_escape(obj['text_sample'][:1200])}</pre></td></tr>"
        for obj in source_page["objects"] if obj["role"] == "shape"
    )
    block_rows = "".join(
        f"<tr><td>{_escape(block['kind'])}<br><span class='tag'>{_escape(block['docling_label'])}</span></td>"
        f"<td>{_escape(block['source_ref']['mapping_status'])}<br>"
        f"<small>{_escape(block['source_ref']['xml_object_path'] or 'no source object')}</small></td>"
        f"<td><pre>{_escape(block['content'][:1200])}</pre></td></tr>"
        for block in (page["blocks"] if page else [])
    )
    result_rows = "".join(
        f"<tr><td>{_escape(r['check_id'])}</td><td>{_escape(r['category'])}</td>"
        f"<td class='{_escape(r['status'])}'>{_escape(r['status'])}</td><td>{_escape(r['detail'])}</td></tr>"
        for r in results
    )
    preview_html = (
        f"<img src='{_escape(preview['relative_path'])}' alt='rendered preview of slide {slide_no}'>"
        f"<p><small>Our own LibreOffice render, not a teacher export. Preview PDF page "
        f"{_escape(preview.get('preview_pdf_page_no'))}, mapping check "
        f"{_escape(page.get('preview_mapping_check') if page else None)}.</small></p>"
        if preview else "<p><small>No preview image for this slide.</small></p>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(suite_page['deck'])} slide {slide_no}</title><style>{STYLE}</style></head>
<body>
<h1>{_escape(suite_page['deck'])} — source slide {slide_no}</h1>
<p><span class="tag">{_escape(suite_page['split'])}</span>
{"".join(f"<span class='tag'>{_escape(c)}</span>" for c in suite_page['categories'])}
visibility: {_escape(source_page['visibility'])}</p>
<div class="grid">
  <div><h2>Preview</h2>{preview_html}</div>
  <div><h2>Source objects</h2><table><tr><th>kind</th><th>object</th><th>source text</th></tr>{source_rows}</table></div>
</div>
<h2>Parser result</h2>
<table><tr><th>kind</th><th>source mapping</th><th>content</th></tr>{block_rows or "<tr><td colspan=3>no blocks</td></tr>"}</table>
<h2>Automatic checks (status: automated only)</h2>
<table><tr><th>check</th><th>category</th><th>status</th><th>detail</th></tr>{result_rows}</table>
<p><small>Automatic checks compare characters, sources and visibility. Whether the meaning
survived is a human question and is listed separately as awaiting human review.</small></p>
</body></html>
"""


def build_comparison_pages(out_dir: Path, suite: dict, inventory_by_deck: dict, normalized_by_deck: dict,
                            evaluation: dict, previews_by_deck: dict) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for suite_page in suite["pages"]:
        deck, slide_no = suite_page["deck"], suite_page["source_slide_no"]
        normalized = normalized_by_deck.get(deck)
        if normalized is None:
            continue
        results = [
            r for r in evaluation["automatic_results"]
            if r["deck"] == deck and r["source_slide_no"] == slide_no
        ]
        page = next((p for p in normalized["pages"] if p["source_slide_no"] == slide_no), None)
        preview = None
        preview_page_no = page.get("preview_pdf_page_no") if page else None
        if preview_page_no:
            match = next(
                (img for img in previews_by_deck.get(deck, []) if img["preview_pdf_page_no"] == preview_page_no),
                None,
            )
            if match:
                preview = dict(match, relative_path=match["relative_path"], preview_pdf_page_no=preview_page_no)
        name = f"{Path(deck).stem}-p{slide_no:02d}.html"
        (out_dir / name).write_text(
            comparison_page(suite_page, inventory_by_deck[deck], normalized, results, preview), encoding="utf-8"
        )
        written.append({"deck": deck, "source_slide_no": slide_no, "split": suite_page["split"], "page": name})

    index = "".join(
        f"<li><a href='{_escape(entry['page'])}'>{_escape(entry['deck'])} slide {entry['source_slide_no']}</a> "
        f"<span class='tag'>{_escape(entry['split'])}</span></li>"
        for entry in written
    )
    (out_dir / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>M0 ODP comparison pages</title><style>{STYLE}</style></head><body>
<h1>M0 ODP comparison pages</h1>
<p>Generated {_escape(utc_now())}. Development and held-out pages are marked and must be read apart.</p>
<ul>{index}</ul></body></html>""",
        encoding="utf-8",
    )
    return written


def statistics(inventory: dict, normalized_by_deck: dict, coverage_by_deck: dict, evaluation: dict, suite: dict) -> dict:
    per_deck = []
    for record in inventory["files"]:
        deck = record["file_name"]
        normalized = normalized_by_deck.get(deck)
        coverage = coverage_by_deck.get(deck, {}).get("totals", {})
        per_deck.append({
            "deck": deck,
            "source_slides": record.get("source_slide_count"),
            "visible_slides": record.get("visible_slide_count"),
            "hidden_slides": record.get("hidden_slide_count"),
            "non_empty_notes": record.get("non_empty_notes_count"),
            "source_objects": record.get("object_totals", {}),
            "blocks": normalized["totals"]["blocks"] if normalized else 0,
            "blocks_with_source_object": normalized["totals"]["blocks_with_source_object"] if normalized else 0,
            "blocks_with_bbox": normalized["totals"]["blocks_with_bbox"] if normalized else 0,
            "source_paragraphs": coverage.get("source_paragraphs"),
            "paragraphs_present": coverage.get("paragraphs_present"),
            "paragraphs_character_exact": coverage.get("paragraphs_character_exact"),
            "paragraphs_split_across_blocks": coverage.get("paragraphs_split_across_blocks"),
            "source_figures": coverage.get("source_figures"),
            "figure_blocks": coverage.get("figure_blocks"),
            "source_tables": coverage.get("source_tables"),
            "table_blocks": coverage.get("table_blocks"),
        })
    return {
        "generated_at": utc_now(),
        "per_deck": per_deck,
        "corpus": {
            "files": inventory["file_count"],
            "source_slides": inventory["source_slide_total"],
            "hidden_slides": inventory["hidden_slide_total"],
            "non_empty_notes": inventory["non_empty_notes_total"],
            "source_paragraphs": sum(d["source_paragraphs"] or 0 for d in per_deck),
            "paragraphs_present": sum(d["paragraphs_present"] or 0 for d in per_deck),
            "paragraphs_character_exact": sum(d["paragraphs_character_exact"] or 0 for d in per_deck),
            "paragraphs_split_across_blocks": sum(d["paragraphs_split_across_blocks"] or 0 for d in per_deck),
            "source_figures": sum(d["source_figures"] or 0 for d in per_deck),
            "figure_blocks": sum(d["figure_blocks"] or 0 for d in per_deck),
            "source_tables": sum(d["source_tables"] or 0 for d in per_deck),
            "table_blocks": sum(d["table_blocks"] or 0 for d in per_deck),
        },
        "automatic_checks": evaluation["automatic_summary"],
        "suite_sha256": suite["suite_sha256"],
        "checks_sha256": evaluation["checks_sha256"],
    }


def questions_evidence(questions: dict, normalized_by_deck: dict, inventory_by_deck: dict) -> dict:
    """For each question: is the evidence it needs still present after parsing?

    This says nothing about retrieval or answers. No agent is run.
    """
    out = []
    for question in questions["questions"]:
        entries = []
        for need in question["evidence"]:
            deck, slide_no = need["deck"], need["source_slide_no"]
            normalized = normalized_by_deck.get(deck)
            inventory_file = inventory_by_deck.get(deck)
            page = next((p for p in (normalized or {"pages": []})["pages"] if p["source_slide_no"] == slide_no), None)
            source_page = inventory_file["pages"][slide_no - 1] if inventory_file else None
            source_figures = sum(
                1 for obj in (source_page or {"objects": []})["objects"]
                if obj["role"] == "shape" and obj["kind"] in {"embedded_graphics", "embedded_image", "group_shape"}
            )
            figure_blocks = sum(1 for b in (page or {"blocks": []})["blocks"] if b["kind"] == "figure")
            text_blocks = sum(1 for b in (page or {"blocks": []})["blocks"] if b["content"].strip())
            entries.append({
                "deck": deck,
                "source_slide_no": slide_no,
                "text_blocks": text_blocks,
                "source_figures": source_figures,
                "figure_blocks": figure_blocks,
                "page_reference": page.get("preview_pdf_page_no") if page else None,
            })
        text_ok = all(e["text_blocks"] > 0 for e in entries)
        figure_ok = (not question["needs_figure"]) or all(
            e["figure_blocks"] >= e["source_figures"] and e["source_figures"] > 0 for e in entries
        )
        out.append({
            "question_id": question["question_id"],
            "needs_figure": question["needs_figure"],
            "evidence": entries,
            "text_evidence_preserved": text_ok,
            "figure_evidence_preserved": figure_ok if question["needs_figure"] else "not_required",
            "verdict": "preserved" if text_ok and (figure_ok or not question["needs_figure"]) else "incomplete",
        })
    return {
        "generated_at": utc_now(),
        "note": "Evidence presence only. No retrieval and no answer accuracy is measured.",
        "questions_sha256": questions["questions_sha256"],
        "results": out,
        "preserved": sum(1 for r in out if r["verdict"] == "preserved"),
        "incomplete": sum(1 for r in out if r["verdict"] == "incomplete"),
    }
