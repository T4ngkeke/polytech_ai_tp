"""What a research run returns, and the two ways it can be stopped from outside."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# An evidence ID: E1, E12.
LABEL_RE = re.compile(r'E[1-9]\d*')
# Evidence cited in an answer: [E1], or several IDs in one bracket: [E1, E2].
CITATION_RE = re.compile(r'\[(E[1-9]\d*(?:\s*[,;]\s*E[1-9]\d*)*)\]')


def cited_ids(text: str) -> set[str]:
    """The evidence IDs an answer cites."""
    return {label for group in CITATION_RE.findall(text) for label in LABEL_RE.findall(group)}


# A bracket that starts like an evidence ID or a handle: [E8.p14.b1], [d6.p24];
# or that names a file or a page: [EASI951_CM_2025.pdf, page 42], [cours, p. 3].
_REFERENCE_RE = re.compile(r'\[[Ed]\s?\d[^\[\]\n]{0,40}\]'
                           r'|\[[^\[\]\n]{0,80}(?:\.pdf\b|\bpages?\s*\d|\bp\.\s*\d)[^\[\]\n]{0,40}\]',
                           re.IGNORECASE)
_CODE_RE = re.compile(r'```[\s\S]*?```|`[^`\n]*`')


# A handle written outside brackets: "(d6.p19)", "d6.p43–d6.p48", "d1.p20.b3".
_BARE_HANDLE_RE = re.compile(r'(?<![\w.\[])d\d+\.[pox]\d+(?:\.b\d+)?(?![\w\]])')


def malformed_refs(text: str) -> list[str]:
    """References outside code that are not valid [E#] citations, such as a
    handle or an ID with extra parts; the student could not open them."""
    prose = _CODE_RE.sub('', text)
    return ([m.group(0) for m in _REFERENCE_RE.finditer(prose)
             if not CITATION_RE.fullmatch(m.group(0))]
            + _BARE_HANDLE_RE.findall(_REFERENCE_RE.sub('', prose)))


class ResearchQuotaExceeded(ValueError):
    """The student's daily token quota does not cover the next model call."""


class ResearchRunStopped(ValueError):
    """The run is no longer running (cancelled by the student, or settled)."""


@dataclass
class ResearchResult:
    answer: str
    citations: list[dict]
    stop_reason: str
    unresolved: list[str]
    tool_calls: int
    model_calls: int
    trace: list[dict] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)
    # A question back to the student (ask_student): {'question', 'options'}.
    clarification: dict | None = None
