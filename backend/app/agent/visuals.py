"""Diagrams and charts in answers: two fixed formats, never free HTML or JavaScript.

Decided 2026-11-20: an answer may draw only

- a Mermaid diagram, in a ```mermaid block (Open WebUI's chat renders the same
  blocks); the page draws it with Mermaid's strict security level;
- a chart, in a ```chart block holding JSON in the shape of ``Chart`` below;
  the page draws it with its own chart component.

A Python run can also show a matplotlib figure (code_tools.py).

``checked(answer)`` runs on every answer before it is saved
(research_runs.finish_run): a chart block that is not a valid ``Chart``, or a
Mermaid block of a type not listed or with a configuration directive, becomes
a plain code block (```json, ```text), so the page shows it as text and never
renders it. Nothing else in the answer changes (its citations stay as they are).
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

VISUAL_NOTE = ('Diagrams and charts: when one makes the answer clearer (a flow of steps, a recursion '
               'tree, classes, states, measures to compare), you may add one, in one of two forms only. '
               'A diagram: a ```mermaid block (flowchart, sequenceDiagram, classDiagram, stateDiagram-v2, '
               'erDiagram, gantt, pie, mindmap, timeline). A chart: a ```chart block holding only JSON '
               '{"type": "bar" | "line" | "scatter" | "pie", "title": "...", "x_label": "...", '
               '"y_label": "...", "labels": [...], "series": [{"name": "...", "values": [numbers]}]}, '
               'one value per label (for scatter, labels are the x values). Never HTML, SVG or '
               'JavaScript. Keep labels short. The text still explains; citations stay in the text, '
               'not inside the block.')

MERMAID_TYPES = frozenset({
    'flowchart', 'graph', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'stateDiagram-v2',
    'erDiagram', 'gantt', 'pie', 'mindmap', 'timeline', 'journey', 'quadrantChart', 'xychart-beta',
    'gitGraph'})
MERMAID_MAX_CHARS = 6000
MAX_POINTS = 200

_BLOCK = re.compile(r'^```[ \t]*(mermaid|chart)[ \t]*\n(.*?)\n[ \t]*```[ \t]*$', re.MULTILINE | re.DOTALL)


class Series(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field('', max_length=60)
    values: list[float | None] = Field(..., min_length=1, max_length=MAX_POINTS)


class Chart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['bar', 'line', 'scatter', 'pie']
    title: str = Field('', max_length=120)
    x_label: str = Field('', max_length=60)
    y_label: str = Field('', max_length=60)
    labels: list[str | float] = Field(..., min_length=1, max_length=MAX_POINTS)
    series: list[Series] = Field(..., min_length=1, max_length=8)

    @model_validator(mode='after')
    def _shape(self):
        if any(len(s.values) != len(self.labels) for s in self.series):
            raise ValueError('one value per label')
        if any(isinstance(label, str) and len(label) > 60 for label in self.labels):
            raise ValueError('label too long')
        if self.type == 'scatter' and not all(isinstance(x, (int, float)) for x in self.labels):
            raise ValueError('scatter labels are numbers')
        if self.type == 'pie' and (len(self.series) != 1 or
                                   any(v is None or v < 0 for v in self.series[0].values)):
            raise ValueError('a pie has one series of values >= 0')
        return self


def mermaid_ok(source: str) -> bool:
    if len(source) > MERMAID_MAX_CHARS or '%%{' in source:      # no init/config directive
        return False
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith('%%'):
            continue
        return line.split()[0].rstrip(':;') in MERMAID_TYPES
    return False


def chart_or_none(source: str) -> Chart | None:
    try:
        return Chart.model_validate(json.loads(source))
    except (ValueError, ValidationError, TypeError):
        return None


def checked(answer: str) -> str:
    """The answer with every diagram or chart block it cannot render made plain text."""
    def one(match: re.Match) -> str:
        kind, source = match.group(1), match.group(2)
        if kind == 'mermaid':
            return match.group(0) if mermaid_ok(source) else f'```text\n{source}\n```'
        chart = chart_or_none(source)
        if chart is None:
            return f'```json\n{source}\n```'
        return '```chart\n' + chart.model_dump_json(exclude_defaults=True) + '\n```'
    return _BLOCK.sub(one, answer) if '```' in answer else answer
