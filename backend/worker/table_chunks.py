"""Search parts of a table block (MinerU's HTML table).

A table is cut only between rows, never inside a cell, so each part is still
an exact slice of the block (citations keep their character ranges), and the
parts cover the whole block. Search does not read that HTML: each part has a
search text, the plain text of its rows (cells joined by " | ", one row per
line, HTML entities decoded, a merged cell repeated in each row and column it
covers) with the table's first row (its header) in front.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

ROW_END = re.compile(r'</tr\s*>', re.IGNORECASE)
# Search text of one part: a few rows. A single longer row is a part of its own.
TARGET_BYTES = 800


@dataclass(frozen=True)
class TablePart:
    start: int
    end: int
    content: str          # content[start:end] of the block
    search_text: str
    byte_count: int


class _Rows(HTMLParser):
    """The rows of a table as lists of (text, rowspan, colspan), in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self.cell: list[str] | None = None
        self.span = (1, 1)
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
        elif self.depth == 1 and tag == 'tr':
            self.rows.append([])
        elif self.depth == 1 and tag in ('td', 'th') and self.rows:
            values = dict(attrs)
            self.cell, self.span = [], (_span(values.get('rowspan')), _span(values.get('colspan')))
        elif tag == 'br' and self.cell is not None:
            self.cell.append(' ')

    def handle_endtag(self, tag):
        if tag == 'table':
            self.depth -= 1
        elif self.depth == 1 and tag in ('td', 'th') and self.cell is not None:
            self.rows[-1].append((' '.join(''.join(self.cell).split()), *self.span))
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def _span(value) -> int:
    try:
        return max(1, min(int(value), 50))
    except (TypeError, ValueError):
        return 1


def row_texts(html: str) -> list[str]:
    """One line per row, cells joined by ' | ', merged cells repeated."""
    parser = _Rows()
    parser.feed(html)
    parser.close()
    grid: dict[tuple[int, int], str] = {}
    for r, row in enumerate(parser.rows):
        c = 0
        for text, rowspan, colspan in row:
            while (r, c) in grid:
                c += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid.setdefault((r + dr, c + dc), text)
            c += colspan
    lines = []
    for r in range(len(parser.rows)):
        cells = [grid[(rr, c)] for (rr, c) in sorted(k for k in grid if k[0] == r)]
        lines.append(' | '.join(cells))
    return lines


def split_table(content: str, *, target_bytes: int = TARGET_BYTES) -> list[TablePart] | None:
    """The search parts of a table block; None when it is not one HTML table
    (the caller then cuts it as text)."""
    if not content.lstrip().lower().startswith('<table'):
        return None
    ends = [m.end() for m in ROW_END.finditer(content)]
    if not ends:
        return None
    rows = row_texts(content)
    if len(rows) != len(ends):            # a nested table or broken HTML: not cut by rows
        return None
    # Row i spans content[ends[i-1]:ends[i]]; the last part also takes the closing tags.
    bounds = [0] + ends[:-1] + [len(content)]
    header = rows[0]
    parts, first = [], 0
    while first < len(rows):
        last, size = first, 0
        while last < len(rows):
            width = len(rows[last].encode()) + 1
            if size and size + width > target_bytes:
                break
            size += width
            last += 1
        lines = rows[first:last]
        text = '\n'.join(lines if first == 0 else [header, *lines])
        start, end = bounds[first], bounds[last]
        parts.append(TablePart(start, end, content[start:end], text, len(content[start:end].encode())))
        first = last
    assert ''.join(part.content for part in parts) == content
    return parts
