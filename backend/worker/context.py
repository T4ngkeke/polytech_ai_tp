"""Accept only a short final retrieval description, never a reasoning transcript."""

import json
import re

MAX_CONTEXT_CHARS = 400
_THINK_CLOSE = re.compile(r"</(?:think|analysis|reasoning)\s*>", re.IGNORECASE)
_THINK_MARKER = re.compile(r"</?(?:think|analysis|reasoning)\b", re.IGNORECASE)
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")

CONTEXT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "chunk_context",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"context": {"type": "string"}},
            "required": ["context"],
            "additionalProperties": False,
        },
    },
}


def _final_part(text: str) -> str:
    # Some servers omit the opening <think> but still emit </think>. Everything
    # before the last closing reasoning tag is therefore excluded as well.
    endings = list(_THINK_CLOSE.finditer(text))
    if endings:
        text = text[endings[-1].end():]
    # An unclosed reasoning block has no usable final answer.
    return "" if _THINK_MARKER.search(text) else text.strip()


def clean_context(value: str | None) -> str:
    """Storage-boundary guard for injected context functions as well as real ones."""
    if not isinstance(value, str):
        return ""
    final = re.sub(r"\s+", " ", _final_part(value)).strip()
    # Reject, rather than truncate, a long explanation: cutting mid-sentence
    # might change its meaning. The caller can fall back to source headings.
    return final if len(final) <= MAX_CONTEXT_CHARS else ""


def parse_context_response(raw: str | None) -> str:
    """Only the JSON context field is eligible for indexing; free text is refused."""
    if not isinstance(raw, str):
        return ""
    final = _final_part(raw)
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", final, re.DOTALL | re.IGNORECASE)
    if fenced:
        final = fenced[1]
    try:
        data = json.loads(final)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict) or set(data) != {"context"}:
        return ""
    value = data["context"]
    if not isinstance(value, str) or _THINK_MARKER.search(value):
        return ""
    return clean_context(value)


def is_heading_only(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(_HEADING.match(line) for line in lines)


def heading_context(chunk) -> str:
    """Use only existing source labels; never guess what a project name means."""
    heading = chunk.section or " > ".join(
        _HEADING.sub("", line).strip() for line in chunk.content.splitlines()
        if _HEADING.match(line)
    )
    # A label can be shortened safely; generated sentences cannot.
    return clean_context(heading[:MAX_CONTEXT_CHARS])
