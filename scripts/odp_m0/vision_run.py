"""Does letting the model read the figure actually add anything?

Three methods over the same twelve figures and the same shared input:

  A  no model at all: native labels and the text around the figure.
  B  the model sees the figure on its own.
  C  the model sees the figure plus the whole page, and still describes only
     the figure.

B and C use the same model, the same schema and the same wording. The only
difference is whether the page image is attached, so a change can be put down
to that and nothing else.

The model is asked for the relations it can see, and for what it cannot confirm.
It never rewrites the native text: its answer is stored beside the picture and
the native labels, never over them.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .common import utc_now

# The shape of an answer. Constrained output is required: this model reasons out
# loud otherwise and the answer never arrives (see the M0 capability probe).
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "colour": {"type": "string"},
            }, "required": ["name", "colour"], "additionalProperties": False},
        },
        "edges": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "directed": {"type": "boolean"},
                "weight": {"type": "string"},
            }, "required": ["from", "to", "directed", "weight"], "additionalProperties": False},
        },
        "colour_meaning": {"type": "string"},
        "cannot_confirm": {"type": "array", "items": {"type": "string"}},
        "needs_bigger_picture": {"type": "boolean"},
    },
    "required": ["nodes", "edges", "colour_meaning", "cannot_confirm", "needs_bigger_picture"],
    "additionalProperties": False,
}

INSTRUCTION = (
    "You are reading one diagram from a course slide. Report only what is visible in the "
    "diagram itself.\n"
    "- nodes: every node you can see. Use the text written in the node as its name; if a node "
    "has no text, name it by position, for example 'top' or 'lower left'. Give its fill colour.\n"
    "- edges: every line between two nodes. Set directed to true only if that line actually has "
    "an arrow head. Put the number written on the line in weight, or an empty string if there is "
    "no number.\n"
    "- colour_meaning: what the colours stand for, only if the image itself says so, for example "
    "through a legend. If nothing in the image says what a colour means, write 'not stated in the image'.\n"
    "- cannot_confirm: anything you are unsure about.\n"
    "- needs_bigger_picture: true if you would need a larger or clearer image.\n"
    "Do not add relations that are not drawn. Do not guess from the topic of the slide."
)

PAGE_NOTE = (
    "\nA picture of the whole slide is attached as well, for context only. Describe the diagram, "
    "not the slide. Still report only relations that are drawn in the diagram."
)


def _data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def ask(endpoint: str, model: str, api_key: str, figure: Path, page: Path | None,
        *, timeout: int = 60, max_tokens: int = 1200) -> dict:
    """One call. No retry: a failure is recorded as a failure."""
    content = [{"type": "text", "text": INSTRUCTION + (PAGE_NOTE if page else "")},
               {"type": "image_url", "image_url": {"url": _data_url(figure)}}]
    if page:
        content.append({"type": "image_url", "image_url": {"url": _data_url(page)}})

    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": {"type": "json_schema",
                             "json_schema": {"name": "figure_reading", "schema": ANSWER_SCHEMA, "strict": True}},
    }
    request = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    started = time.time()
    record = {"started_at": utc_now(), "model": model, "with_page_image": bool(page)}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        return dict(record, status="http_error", code=error.code,
                    detail=error.read()[:300].decode("utf-8", "replace"),
                    seconds=round(time.time() - started, 2))
    except Exception as error:
        return dict(record, status="error", detail=f"{type(error).__name__}: {str(error)[:200]}",
                    seconds=round(time.time() - started, 2))

    record["seconds"] = round(time.time() - started, 2)
    record["usage"] = payload.get("usage")
    text = payload["choices"][0]["message"]["content"]
    record["raw_answer"] = text[:4000]
    try:
        record["answer"] = json.loads(text)
        record["status"] = "ok"
    except json.JSONDecodeError:
        record["status"] = "unparsable"
    return record


def method_a(target: dict) -> dict:
    """No model: what the native extraction alone already gives."""
    return {
        "method": "A",
        "status": "ok",
        "figure_asset": target["figure"].get("path"),
        "native_labels": target["facts"].get("node_labels", []) if target.get("facts") else [],
        "native_text_lines": target["native_text_lines"],
        "slide_title": target["slide_title"],
        "claims_relations": False,
        "note": "the figure is kept and searchable through the text around it; no relation is claimed",
    }
