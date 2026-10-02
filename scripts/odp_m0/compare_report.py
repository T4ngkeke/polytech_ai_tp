"""Side by side comparison pages: source, MinerU, Docling.

One page per frozen slide. Each shows the source objects, what each parser
produced, and every check with its verdict and the evidence behind it.
Automatic checks are labelled automated; the human ones stay pending.
"""
from __future__ import annotations

import html
from pathlib import Path

from .common import utc_now
from .report import STYLE


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _blocks_html(normalized: dict, slide_no: int) -> str:
    page = next((p for p in normalized["pages"] if p["source_slide_no"] == slide_no), None)
    if not page:
        return "<p>no page</p>"
    rows = []
    for block in page["blocks"]:
        asset = block["assets"][0]["path"] if block["assets"] else ""
        rows.append(
            f"<tr><td>{_e(block['kind'])}</td>"
            f"<td>{_e(block['source_ref']['mapping_status'])}</td>"
            f"<td><pre>{_e(block['content'][:600])}</pre>{('<small>' + _e(asset) + '</small>') if asset else ''}</td></tr>"
        )
    return f"<table><tr><th>kind</th><th>source</th><th>content</th></tr>{''.join(rows)}</table>"


def page_html(suite_page: dict, record: dict, results_by_parser: dict, normalized_by_parser: dict) -> str:
    slide_no = suite_page["source_slide_no"]
    source_page = record["pages"][slide_no - 1]
    source_rows = "".join(
        f"<tr><td>{_e(o['kind'])}</td><td><pre>{_e(o['text_sample'][:600])}</pre></td></tr>"
        for o in source_page["objects"] if o["role"] == "shape"
    )
    check_rows = []
    all_ids = []
    for parser, results in results_by_parser.items():
        for result in results:
            if result["check_id"] not in all_ids:
                all_ids.append(result["check_id"])
    for check_id in all_ids:
        cells = []
        for parser, results in results_by_parser.items():
            match = next((r for r in results if r["check_id"] == check_id), None)
            if match is None:
                cells.append("<td>-</td>")
                continue
            evidence = ""
            if match.get("evidence"):
                evidence = "<br><small>" + _e(str(match["evidence"])[:200]) + "</small>"
            cells.append(
                f"<td class='{_e(match['status'])}'>{_e(match['status'])}"
                f"<br><small>{_e(match.get('detail'))}</small>{evidence}</td>"
            )
        check_rows.append(f"<tr><td><code>{_e(check_id)}</code></td>{''.join(cells)}</tr>")

    parser_columns = "".join(f"<th>{_e(name)}</th>" for name in results_by_parser)
    parser_blocks = "".join(
        f"<div><h3>{_e(name)}</h3>{_blocks_html(normalized, slide_no)}</div>"
        for name, normalized in normalized_by_parser.items()
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(suite_page['deck'])} slide {slide_no}</title><style>{STYLE}</style></head><body>
<h1>{_e(suite_page['deck'])} — source slide {slide_no}</h1>
<p><span class="tag">{_e(suite_page['split'])}</span>
{''.join(f"<span class='tag'>{_e(c)}</span>" for c in suite_page['categories'])}</p>
<h2>Source objects</h2>
<table><tr><th>kind</th><th>source text</th></tr>{source_rows}</table>
<h2>What each parser produced</h2>
<div class="grid">{parser_blocks}</div>
<h2>Checks (automated only)</h2>
<table><tr><th>check</th>{parser_columns}</tr>{''.join(check_rows)}</table>
<p><small>The held-out pages were already seen in the first round, so this is a shared
comparison set, not a fresh held-out set. Whether the meaning survived is a human
question and is listed separately as awaiting human review.</small></p>
</body></html>"""


def build(out_dir: Path, suite: dict, inventory_by_deck: dict, evaluations: dict,
          normalized: dict) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for suite_page in suite["pages"]:
        deck, slide_no = suite_page["deck"], suite_page["source_slide_no"]
        results_by_parser = {
            parser: [r for r in evaluation["automatic_results"]
                     if r["deck"] == deck and r["source_slide_no"] == slide_no]
            for parser, evaluation in evaluations.items()
        }
        normalized_by_parser = {
            parser: docs[deck] for parser, docs in normalized.items() if deck in docs
        }
        if not normalized_by_parser:
            continue
        name = f"{Path(deck).stem}-p{slide_no:02d}.html"
        (out_dir / name).write_text(
            page_html(suite_page, inventory_by_deck[deck], results_by_parser, normalized_by_parser),
            encoding="utf-8")
        written.append({"deck": deck, "source_slide_no": slide_no, "split": suite_page["split"], "page": name})

    index = "".join(
        f"<li><a href='{_e(w['page'])}'>{_e(w['deck'])} slide {w['source_slide_no']}</a> "
        f"<span class='tag'>{_e(w['split'])}</span></li>" for w in written)
    (out_dir / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MinerU vs Docling on ODP</title><style>{STYLE}</style></head><body>
<h1>MinerU vs Docling on ODP</h1>
<p>Generated {_e(utc_now())}. One page per frozen slide, both parsers under the same checks.</p>
<ul>{index}</ul></body></html>""", encoding="utf-8")
    return written
