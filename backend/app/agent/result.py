"""What a research run returns, and the two ways it can be stopped from outside."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# An evidence ID cited in an answer: [E1], [E12]. One ID per bracket.
CITATION_RE = re.compile(r'\[(E[1-9]\d*)\]')


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
