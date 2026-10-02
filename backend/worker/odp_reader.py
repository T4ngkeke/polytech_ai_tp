"""Native ODF reader: one ODP file in, one ParsedDocument out.

M2-A production module. It reads the OpenDocument XML itself, so the source
text, the explicit whitespace, the table structure and the object identities all
come from the file rather than from a model or from a converted document. It
never imports the M0 experiment, never calls a model and never writes to the
source file.

What it deliberately does not do:

* it does not convert a formula object to LaTeX; the original object is kept as
  an asset and the block is sent to review;
* it does not describe a drawing; a vector object gets its native labels, its
  embedded pictures and a request for a rendered preview;
* it does not drop an object it cannot classify; that object becomes an issue
  with its native path, so the gap is visible;
* it does not read speaker notes text, and it does not read hidden slides.
"""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from backend.worker.document_ir import (
    ParsedAsset,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParseIssue,
    SourceRef,
    stable_id,
)
from backend.worker.odf_package import (
    FORMULA_MIME,
    GRAPHICS_MIME_PREFIX,
    OdfLimits,
    OdfPackage,
    OdfPackageError,
    Q,
    SPREADSHEET_MIME_PREFIX,
    open_package,
    package_relative_href,
    external_scheme,
    prefixed_name,
    sha256_bytes,
)

READER_VERSION = 'odp-reader-m2a-2'
IDENTITY_VERSION = 'odp-identity-1'

CONTENT_ENTRY = 'content.xml'
STYLES_ENTRY = 'styles.xml'

# Subtrees whose text is never read: speaker notes and author annotations are
# not visible content, and D02 keeps them away from students entirely.
EXCLUDED_TEXT_TAGS = {'presentation:notes', 'office:annotation', 'office:event-listeners'}
# Present on slides and not content. Skipped with a reason, not called unknown.
NON_CONTENT_TAGS = {
    'office:forms', 'draw:layer-set', 'anim:par', 'anim:seq', 'anim:audio',
    'presentation:animations', 'office:annotation',
}
# Shapes that draw something without carrying a text body of their own.
VECTOR_TAGS = {
    'draw:custom-shape', 'draw:line', 'draw:connector', 'draw:polyline', 'draw:path',
    'draw:rect', 'draw:ellipse', 'draw:circle', 'draw:measure', 'draw:caption',
    'draw:polygon', 'draw:regular-polygon', 'dr3d:scene',
}
NAVIGATION_CLASSES = {'page-number', 'date-time', 'footer', 'header'}
HEADING_CLASSES = {'title', 'subtitle'}

IMAGE_MIME_BY_SUFFIX = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
    '.svg': 'image/svg+xml', '.tif': 'image/tiff', '.tiff': 'image/tiff', '.bmp': 'image/bmp',
    '.emf': 'image/x-emf', '.wmf': 'image/x-wmf', '.webp': 'image/webp',
}


class OdpReadError(RuntimeError):
    """The file cannot be parsed at all. No partial IR is invented for it."""


@dataclass(frozen=True)
class ReaderOptions:
    """Resource bounds for one parse.

    Reaching a bound is always reported on the block it affected. Content is
    never shortened quietly to stay inside one.
    """

    max_table_cells: int = 20_000
    max_table_rows: int = 5_000
    max_blocks_per_page: int = 2_000
    max_assets: int = 5_000
    max_asset_bytes: int = 32 * 1024 * 1024
    # One text:s can ask for any number of spaces, and one shape can hold any
    # number of paragraphs. Both are bounded before a single character is built.
    max_space_run: int = 4_096
    max_text_chars: int = 1_000_000
    reading_order_band_cm: float = 1.0

    def as_dict(self) -> dict:
        return {
            'max_table_cells': self.max_table_cells,
            'max_table_rows': self.max_table_rows,
            'max_blocks_per_page': self.max_blocks_per_page,
            'max_assets': self.max_assets,
            'max_asset_bytes': self.max_asset_bytes,
            'max_space_run': self.max_space_run,
            'max_text_chars': self.max_text_chars,
            'reading_order_band_cm': self.reading_order_band_cm,
        }


def config_hash(options: ReaderOptions, limits: OdfLimits) -> str:
    payload = {
        'reader_version': READER_VERSION,
        'identity_version': IDENTITY_VERSION,
        'options': options.as_dict(),
        'limits': limits.as_dict(),
    }
    return sha256_bytes(json.dumps(payload, sort_keys=True).encode('utf-8'))[:32]


# --------------------------------------------------------------------------
# XML helpers
# --------------------------------------------------------------------------

def _attr(element: ET.Element, prefix: str, name: str) -> str | None:
    return element.attrib.get(Q[prefix] + name)


def _length_cm(raw: str | None) -> float | None:
    """A length in centimetres, or None when the unit is not one we know."""
    if not raw:
        return None
    text = raw.strip()
    for unit, factor in (('cm', 1.0), ('mm', 0.1), ('in', 2.54), ('pt', 2.54 / 72), ('pc', 2.54 / 6)):
        if text.endswith(unit):
            try:
                return round(float(text[: -len(unit)]) * factor, 4)
            except ValueError:
                return None
    return None


class TextBudget:
    """How much text one shape may produce, checked before anything is built.

    A ``text:s`` element carries a repeat count written in the file. Building
    the spaces first and checking afterwards means the file decides how much
    memory we use. Here the count is compared with the bound first, and what is
    left out is written down: a caller that sees a violation marks the block
    incomplete instead of presenting a shortened line as the source text.
    """

    def __init__(self, options: 'ReaderOptions | None' = None) -> None:
        options = options or ReaderOptions()
        self.max_space_run = max(options.max_space_run, 1)
        self.max_chars = max(options.max_text_chars, 1)
        self.used = 0
        self.violations: list[str] = []

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_chars

    def room(self) -> int:
        return max(self.max_chars - self.used, 0)

    def take(self, piece: str) -> str:
        """The part of ``piece`` that fits, with anything left out recorded."""
        if not piece:
            return ''
        room = self.room()
        if len(piece) <= room:
            self.used += len(piece)
            return piece
        if room and not self.violations:
            self.violations.append(
                f'the text of this object passes the {self.max_chars} character bound and was cut')
        elif not room and not self.violations:
            self.violations.append(
                f'the text of this object passes the {self.max_chars} character bound and was cut')
        self.used = self.max_chars
        return piece[:room]

    def spaces(self, raw_count: str | None) -> str:
        """The spaces a text:s asks for, bounded before they are built."""
        try:
            count = int(raw_count) if raw_count else 1
        except ValueError:
            count = 1
        if count < 1:
            return ''
        if count > self.max_space_run:
            self.violations.append(
                f'a run of {count} spaces passes the {self.max_space_run} space bound and was cut')
            count = self.max_space_run
        return self.take(' ' * min(count, self.room()))


def _text_of(element: ET.Element, budget: TextBudget) -> str:
    """Inline text of one paragraph with the explicit ODF whitespace decoded.

    text:s carries a repeat count, text:tab is a tab, text:line-break a newline.
    Reading itertext() alone loses all three, which is exactly the indentation a
    code example depends on. Nothing here is stripped or collapsed; the only
    thing that can shorten the result is the budget, and that is reported.
    """
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            if budget.exhausted:
                return
            tag = prefixed_name(child.tag)
            if tag in EXCLUDED_TEXT_TAGS:
                if child.tail:
                    out.append(budget.take(child.tail))
                continue
            if tag == 'text:s':
                out.append(budget.spaces(_attr(child, 'text', 'c')))
            elif tag == 'text:tab':
                out.append(budget.take('\t'))
            elif tag == 'text:line-break':
                out.append(budget.take('\n'))
            else:
                if child.text:
                    out.append(budget.take(child.text))
                walk(child)
            if child.tail:
                out.append(budget.take(child.tail))

    if element.text:
        out.append(budget.take(element.text))
    walk(element)
    return ''.join(out)


def _paragraphs(element: ET.Element, budget: TextBudget) -> list[str]:
    """Every text:p / text:h under this element, in document order."""
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            if budget.exhausted:
                return
            tag = prefixed_name(child.tag)
            if tag in EXCLUDED_TEXT_TAGS:
                continue
            if tag in ('text:p', 'text:h'):
                out.append(_text_of(child, budget))
                continue
            walk(child)

    walk(element)
    return out


def _shape_text(element: ET.Element, budget: TextBudget) -> str:
    """The text body of one shape, paragraph breaks kept as newlines."""
    return '\n'.join(_paragraphs(element, budget))


def _object_path(page_index: int, trail: list[tuple[str, int]]) -> str:
    """The XML path of one object inside the file, 1-based at every step.

    It is stable for repeated parses of the same file. It says nothing about
    stability across file versions, and is combined with the file hash so that
    it cannot be read as if it did.
    """
    parts = [f'/office:document-content/office:body/office:presentation/draw:page[{page_index}]']
    parts.extend(f'{tag}[{index}]' for tag, index in trail)
    return '/'.join(parts)


def _collect_drawing_page_styles(root: ET.Element) -> dict[str, dict]:
    styles: dict[str, dict] = {}
    for style in root.iter(Q['style'] + 'style'):
        if _attr(style, 'style', 'family') != 'drawing-page':
            continue
        name = _attr(style, 'style', 'name')
        if not name:
            continue
        visibility = None
        for props in style.iter(Q['style'] + 'drawing-page-properties'):
            visibility = _attr(props, 'presentation', 'visibility') or visibility
        styles[name] = {'parent': _attr(style, 'style', 'parent-style-name'), 'visibility': visibility}
    return styles


def _resolve_visibility(style_name: str | None, styles: dict) -> tuple[str, str]:
    """Follow the drawing-page style chain. Returns (visibility, why we say so)."""
    if not style_name:
        return 'visible', 'the slide has no drawing-page style; the ODF default is visible'
    seen: list[str] = []
    name: str | None = style_name
    while name and name in styles and name not in seen:
        seen.append(name)
        entry = styles[name]
        if entry['visibility']:
            return (
                'hidden' if entry['visibility'] == 'hidden' else 'visible',
                f"style chain {' -> '.join(seen)} sets presentation:visibility={entry['visibility']}",
            )
        name = entry['parent']
    chain = ' -> '.join(seen) if seen else style_name
    return 'visible', f'style chain {chain} sets no presentation:visibility; the ODF default is visible'


def _positioned_text_styles(content_root: ET.Element, styles_root: ET.Element) -> set[str]:
    """Styles whose vertical text position changes the meaning of flattened math."""
    definitions = {}
    for root in (styles_root, content_root):
        for style in root.iter(Q['style'] + 'style'):
            name = _attr(style, 'style', 'name')
            if not name:
                continue
            props = style.find(Q['style'] + 'text-properties')
            position = _attr(props, 'style', 'text-position') if props is not None else None
            definitions[name] = (_attr(style, 'style', 'parent-style-name'), position)

    def positioned(name: str, seen: set[str]) -> bool:
        if name in seen or name not in definitions:
            return False
        parent, position = definitions[name]
        if position is not None:
            first = position.split()[0] if position.split() else ''
            try:
                return float(first.removesuffix('%')) != 0
            except ValueError:
                # Unrecognized text positioning is uncertain, not a proof of
                # ordinary baseline text.
                return first not in ('', '0', '0%')
        return positioned(parent, seen | {name}) if parent else False

    return {name for name in definitions if positioned(name, set())}


def _positioned_text_refs(element: ET.Element, styles: set[str]) -> tuple[str, ...]:
    """Find visible styled text in one source object; never inspect notes."""
    found = set()

    def walk(node: ET.Element) -> None:
        if prefixed_name(node.tag) in EXCLUDED_TEXT_TAGS:
            return
        name = _attr(node, 'text', 'style-name')
        if name in styles and ''.join(node.itertext()).strip():
            found.add(name)
        for child in node:
            walk(child)

    walk(element)
    return tuple(sorted(found))


class SlideSizes:
    """Page size per master page, read from the layouts the file declares.

    A deck can mix layouts, so there is no single slide size to apply to every
    page. A page whose master or layout cannot be resolved gets no size at all,
    and the reader says so on that page rather than borrowing the first layout
    it happened to find.
    """

    def __init__(self, content_root: ET.Element, styles_root: ET.Element) -> None:
        self.layouts: dict[str, tuple[float, float]] = {}
        self.master_layout: dict[str, str] = {}
        for root in (styles_root, content_root):
            for layout in root.iter(Q['style'] + 'page-layout'):
                name = _attr(layout, 'style', 'name')
                if not name:
                    continue
                for props in layout.iter(Q['style'] + 'page-layout-properties'):
                    width = _length_cm(_attr(props, 'fo', 'page-width'))
                    height = _length_cm(_attr(props, 'fo', 'page-height'))
                    if width and height:
                        self.layouts.setdefault(name, (width, height))
                        break
            for master in root.iter(Q['style'] + 'master-page'):
                name = _attr(master, 'style', 'name')
                layout_name = _attr(master, 'style', 'page-layout-name')
                if name and layout_name:
                    self.master_layout.setdefault(name, layout_name)

    def for_master(self, master_name: str | None) -> tuple[float | None, float | None]:
        if not master_name:
            return None, None
        layout_name = self.master_layout.get(master_name)
        if not layout_name:
            return None, None
        size = self.layouts.get(layout_name)
        return size if size else (None, None)

    @property
    def known_masters(self) -> int:
        return len(self.master_layout)


def _fonts(root: ET.Element) -> list[str]:
    names = set()
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.endswith('}font-name') or key.endswith('}font-family'):
                cleaned = value.strip('\'"').strip()
                if cleaned:
                    names.add(cleaned)
    return sorted(names)


def _external_references(root: ET.Element) -> list[str]:
    """External hrefs under this element, skipping notes and annotations.

    It is called on the visible slides only. Running it over the whole file
    would put a link that exists solely on a hidden slide, or in speaker notes,
    into a result that is supposed to hold visible content.
    """
    refs = set()

    def walk(node: ET.Element) -> None:
        for child in node:
            if prefixed_name(child.tag) in EXCLUDED_TEXT_TAGS:
                continue
            href = child.attrib.get(Q['xlink'] + 'href')
            if href and external_scheme(href):
                refs.add(href)
            walk(child)

    href = root.attrib.get(Q['xlink'] + 'href')
    if href and external_scheme(href):
        refs.add(href)
    walk(root)
    return sorted(refs)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

@dataclass
class TableGrid:
    """A table with explicit coordinates.

    ``rows`` is a list of ``{'row_index', 'cells'}``. Each cell carries its own
    ``row`` and ``column``, so trailing padding can be left out without moving
    any cell that is really there, and an empty row in the middle keeps its
    place instead of collapsing into its neighbour.
    """

    rows: list[dict] = field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = None
    declared_cells: int = 0
    read_cells: int = 0
    dropped_trailing_empty_columns: int = 0
    dropped_trailing_empty_rows: int = 0
    merged_cells: int = 0
    covered_cells: int = 0

    def as_text(self) -> str:
        """A readable rendering. ``rows`` stays the authoritative structure."""
        return '\n'.join('\t'.join(cell['text'] for cell in row['cells']) for row in self.rows)

    def to_dict(self) -> dict:
        return {
            'rows': [{'row_index': row['row_index'], 'cells': [dict(cell) for cell in row['cells']]}
                     for row in self.rows],
            'row_count': len(self.rows),
            'column_count': max((len(row['cells']) for row in self.rows), default=0),
            'truncated': self.truncated,
            'truncation_reason': self.truncation_reason,
            'declared_cells': self.declared_cells,
            'read_cells': self.read_cells,
            'dropped_trailing_empty_columns': self.dropped_trailing_empty_columns,
            'dropped_trailing_empty_rows': self.dropped_trailing_empty_rows,
            'merged_cells': self.merged_cells,
            'covered_cells': self.covered_cells,
        }


def _repeat(element: ET.Element, name: str) -> int:
    raw = element.attrib.get(Q['table'] + name)
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(value, 1)


@dataclass
class _CellRun:
    """One ``table:table-cell`` before its repeat is expanded."""

    text: str
    covered: bool
    columns_spanned: int
    rows_spanned: int
    repeat: int


def read_table(table: ET.Element, *, max_cells: int, max_rows: int = 5_000,
               budget: TextBudget | None = None) -> TableGrid:
    """Read one table:table into a grid with its coordinates kept.

    A repeat carries real content, so repeats are expanded rather than cut at a
    fixed number. What is not expanded is the padding a spreadsheet writes: the
    empty repeated cells at the end of a row, and the empty repeated rows after
    the last row that has anything in it. Those are counted and reported.

    An empty row between two rows that do have content is not padding. It is
    kept, so the row below it keeps its row number.

    Every repeat is compared with the budget before it is built, so the number
    written in the file cannot decide how much memory this costs. When a budget
    is reached the grid stops, says so and says why; nothing is cut in silence.
    """
    grid = TableGrid()
    budget = budget or TextBudget()
    pending_empty_rows = []

    def stop(reason: str) -> TableGrid:
        grid.truncated = True
        grid.truncation_reason = reason
        return grid

    def materialise(runs: list[_CellRun], count: int) -> bool:
        """Write ``count`` copies of one parsed row. False when a budget stops us."""
        nonlocal pending_empty_rows
        width = sum(run.repeat for run in runs)
        for _ in range(count):
            if len(grid.rows) >= max_rows:
                stop(f'the table passes the {max_rows} row bound')
                return False
            if grid.read_cells + width > max_cells:
                stop(f'the table passes the {max_cells} cell bound')
                return False
            row_index = len(grid.rows)
            cells: list[dict] = []
            column = 0
            for run in runs:
                for copy_index in range(run.repeat):
                    cells.append({
                        'row': row_index,
                        'column': column,
                        'text': run.text,
                        'covered': run.covered,
                        'columns_spanned': run.columns_spanned,
                        'rows_spanned': run.rows_spanned,
                        'repeated_from_index': copy_index if run.repeat > 1 else None,
                        'repeat_count': run.repeat,
                    })
                    column += 1
            grid.rows.append({'row_index': row_index, 'cells': cells})
            grid.read_cells += width
        return True

    def flush_pending() -> bool:
        """Restore each blank row's own cells, not the next content row's width."""
        for runs, count in pending_empty_rows:
            if not materialise(runs, count):
                return False
        pending_empty_rows.clear()
        return True

    for row in table.iter(Q['table'] + 'table-row'):
        row_repeat = _repeat(row, 'number-rows-repeated')
        runs: list[_CellRun] = []
        for cell in row:
            tag = prefixed_name(cell.tag)
            if tag not in ('table:table-cell', 'table:covered-table-cell'):
                continue
            column_repeat = _repeat(cell, 'number-columns-repeated')
            grid.declared_cells += column_repeat
            spanned_columns = _repeat(cell, 'number-columns-spanned')
            spanned_rows = _repeat(cell, 'number-rows-spanned')
            covered = tag == 'table:covered-table-cell'
            if covered:
                grid.covered_cells += column_repeat
            if spanned_columns > 1 or spanned_rows > 1:
                grid.merged_cells += 1
            runs.append(_CellRun(
                text='\n'.join(_paragraphs(cell, budget)),
                covered=covered,
                columns_spanned=spanned_columns,
                rows_spanned=spanned_rows,
                repeat=column_repeat,
            ))
        original_runs = list(runs)
        # Padding at the end of a row: empty repeated cells the sheet wrote to
        # fill its width. Dropping them loses no content, and the cells that
        # stay keep their own column numbers.
        while (runs and not runs[-1].text and runs[-1].repeat > 1
               and not runs[-1].covered and runs[-1].columns_spanned == 1
               and runs[-1].rows_spanned == 1):
            grid.dropped_trailing_empty_columns += runs.pop().repeat
        if not any(run.text or run.covered or run.columns_spanned > 1 or run.rows_spanned > 1
                   for run in runs):
            # Kept back: it is padding only if nothing with content follows.
            pending_empty_rows.append((original_runs, row_repeat))
            continue
        if not flush_pending():
            return grid
        if not materialise(runs, row_repeat):
            return grid

    grid.dropped_trailing_empty_rows += sum(count for _, count in pending_empty_rows)
    return grid


# --------------------------------------------------------------------------
# The reader itself
# --------------------------------------------------------------------------

@dataclass
class _PageContext:
    page_id: str
    page_index: int
    slide_width_cm: float | None
    slide_height_cm: float | None
    blocks: list[ParsedBlock] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    geometry: list[tuple[float, float] | None] = field(default_factory=list)
    attributed: set[str] = field(default_factory=set)
    visited: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


class _Reader:
    def __init__(self, package: OdfPackage, options: ReaderOptions, *, positioned_styles: set[str] | None = None) -> None:
        self.package = package
        self.options = options
        self.positioned_styles = positioned_styles or set()
        self.file_sha = package.sha256
        self.assets: list[ParsedAsset] = []
        self.asset_by_entry: dict[str, ParsedAsset] = {}
        self.document_issues: list[ParseIssue] = []

    # -- identity ------------------------------------------------------

    def page_id(self, slide_no: int) -> str:
        return stable_id(IDENTITY_VERSION, self.file_sha, 'page', str(slide_no))

    def block_id(self, object_path: str, discriminator: str = '') -> str:
        return stable_id(IDENTITY_VERSION, self.file_sha, 'block', object_path, discriminator)

    def asset_id(self, entry: str) -> str:
        return stable_id(IDENTITY_VERSION, self.file_sha, 'asset', entry)

    # -- assets --------------------------------------------------------

    def add_asset(self, entry: str, *, kind: str, page_id: str, source_object_id: str,
                  mime: str | None = None) -> ParsedAsset | None:
        """Register one package entry as an asset. Returns None with the caller
        left to report when the entry is not available."""
        existing = self.asset_by_entry.get(entry)
        if existing is not None:
            return existing
        if len(self.assets) >= self.options.max_assets:
            return None
        data = self.package.read(entry)
        if len(data) > self.options.max_asset_bytes:
            return None
        asset = ParsedAsset(
            asset_id=self.asset_id(entry),
            kind=kind,
            mime=mime or self.package.media_type(entry) or _guess_mime(entry),
            sha256=sha256_bytes(data),
            size_bytes=len(data),
            data=data,
            source_entry=entry,
            source_object_id=source_object_id,
            page_id=page_id,
            access_class='teacher_original',
        )
        self.assets.append(asset)
        self.asset_by_entry[entry] = asset
        return asset

    # -- blocks --------------------------------------------------------

    def make_block(self, context: _PageContext, *, kind: str, content: str, object_path: str,
                   element: ET.Element | None, slide_no: int, extraction_method: str,
                   source_text: str | None = None, asset_ids: tuple[str, ...] = (),
                   group_path: tuple[str, ...] = (), members: tuple[str, ...] = (),
                   completeness: str = 'unknown', preview_required: bool = False,
                   preview_object_id: str | None = None, preview_scope: str = 'object',
                   table_data: dict | None = None,
                   package_entries: tuple[str, ...] = (), issues: tuple[ParseIssue, ...] = (),
                   discriminator: str = '', budget: TextBudget | None = None) -> ParsedBlock:
        bbox, bbox_basis = self.bbox_of(element, context)
        issues = tuple(issues)
        if element is not None and kind in ('heading', 'text', 'table', 'navigation'):
            positioned = _positioned_text_refs(element, self.positioned_styles)
            if positioned:
                completeness = 'incomplete'
                issues += (_issue(
                    'content_review',
                    'ODF superscript/subscript styling is not represented in flattened text; '
                    'verify the mathematical meaning against the page preview',
                    detail={'style_names': list(positioned), 'known_limitation': 'positioned_text_flattened'},
                ),)
        if budget is not None and budget.violations:
            # A bound that changed the text is a gap in the source, not a
            # detail: the block says so and stops calling itself complete.
            completeness = 'incomplete'
            issues = issues + tuple(
                _issue('extraction_failure', message) for message in budget.violations
            )
        block = ParsedBlock(
            block_id=self.block_id(object_path, discriminator),
            page_id=context.page_id,
            order_index=len(context.blocks),
            kind=kind,
            content=content,
            content_format='plain',
            source_text=source_text,
            group_path=group_path,
            asset_ids=tuple(asset_ids),
            source_completeness=completeness,
            preview_required=preview_required,
            preview_object_id=(
                None if preview_scope == 'page'
                else preview_object_id or (object_path if preview_required else None)
            ),
            preview_scope=preview_scope,
            table_data=table_data,
            source_ref=SourceRef(
                file_sha256=self.file_sha,
                source_slide_no=slide_no,
                native_object_ids=(object_path, *members),
                native_draw_names=tuple(
                    name for name in [_attr(element, 'draw', 'name') if element is not None else None] if name
                ),
                package_entries=tuple(package_entries),
                extraction_method=extraction_method,
                identity_version=IDENTITY_VERSION,
                bbox=bbox,
                bbox_basis=bbox_basis,
            ),
        )
        for issue in issues:
            block.issues.append(
                ParseIssue(code=issue.code, scope='block', message=issue.message,
                           page_id=context.page_id, block_id=block.block_id,
                           resolution=issue.resolution, detail=issue.detail)
            )
        context.blocks.append(block)
        context.geometry.append(_position_cm(element))
        context.attributed.add(object_path)
        context.attributed.update(members)
        return block

    def bbox_of(self, element: ET.Element | None, context: _PageContext):
        if element is None or not context.slide_width_cm or not context.slide_height_cm:
            return None, None
        x = _length_cm(_attr(element, 'svg', 'x'))
        y = _length_cm(_attr(element, 'svg', 'y'))
        width = _length_cm(_attr(element, 'svg', 'width'))
        height = _length_cm(_attr(element, 'svg', 'height'))
        if x is None or y is None or width is None or height is None:
            return None, None
        return (
            round(x / context.slide_width_cm, 4),
            round(y / context.slide_height_cm, 4),
            round((x + width) / context.slide_width_cm, 4),
            round((y + height) / context.slide_height_cm, 4),
        ), 'native_odf_geometry'

    def note(self, context: _PageContext, code: str, message: str, *, block_id: str | None = None,
             detail: dict | None = None) -> None:
        context.issues.append(ParseIssue(
            code=code, scope='page', message=message, page_id=context.page_id,
            block_id=block_id, detail=detail or {},
        ))


def _guess_mime(entry: str) -> str:
    suffix = posixpath.splitext(entry)[1].lower()
    return IMAGE_MIME_BY_SUFFIX.get(suffix, 'application/octet-stream')


def _position_cm(element: ET.Element | None) -> tuple[float, float] | None:
    if element is None:
        return None
    x = _length_cm(_attr(element, 'svg', 'x'))
    y = _length_cm(_attr(element, 'svg', 'y'))
    if x is None or y is None:
        return None
    return x, y


def _issue(code: str, message: str, *, resolution: str = 'pending', detail: dict | None = None) -> ParseIssue:
    """A block issue built before the block id exists; make_block rebinds it."""
    return ParseIssue(code=code, scope='document', message=message, resolution=resolution,
                      detail=detail or {})


def read_odp(path: Path, *, limits: OdfLimits | None = None,
             options: ReaderOptions | None = None) -> ParsedDocument:
    """Parse one ODP file. The file is opened read-only and never modified."""
    limits = limits or OdfLimits()
    options = options or ReaderOptions()
    path = Path(path)
    try:
        package = open_package(path, limits)
    except OdfPackageError as exc:
        raise OdpReadError(str(exc)) from exc

    with package:
        if not package.has(CONTENT_ENTRY):
            raise OdpReadError(f'{path.name}: the package has no {CONTENT_ENTRY}')
        try:
            content = package.xml(CONTENT_ENTRY)
            styles_root = package.xml(STYLES_ENTRY) if package.has(STYLES_ENTRY) else ET.Element('empty')
        except OdfPackageError as exc:
            raise OdpReadError(str(exc)) from exc

        styles = _collect_drawing_page_styles(content)
        for name, entry in _collect_drawing_page_styles(styles_root).items():
            styles.setdefault(name, entry)
        sizes = SlideSizes(content, styles_root)

        reader = _Reader(package, options, positioned_styles=_positioned_text_styles(content, styles_root))
        slides = list(content.iter(Q['draw'] + 'page'))
        hidden: list[int] = []
        notes_slides = 0
        pages: list[ParsedPage] = []
        external_refs: set[str] = set()
        pages_without_size: list[int] = []

        for slide_no, slide in enumerate(slides, start=1):
            visibility, basis = _resolve_visibility(_attr(slide, 'draw', 'style-name'), styles)
            has_notes = _notes_carry_text(slide)
            if has_notes:
                notes_slides += 1
            if visibility == 'hidden':
                # Nothing from a hidden slide reaches the result, not even the
                # links it carries: an address that exists only there would say
                # what the hidden slide is about.
                hidden.append(slide_no)
                continue

            # Each page uses the size of its own master page.
            width_cm, height_cm = sizes.for_master(_attr(slide, 'draw', 'master-page-name'))
            page_id = reader.page_id(slide_no)
            context = _PageContext(
                page_id=page_id, page_index=slide_no,
                slide_width_cm=width_cm, slide_height_cm=height_cm,
            )
            external_refs.update(_external_references(slide))
            _walk_container(reader, context, slide, slide_no, trail=[], group_path=())
            _check_reading_order(reader, context, options)
            _check_coverage(reader, context)
            if width_cm is None or height_cm is None:
                pages_without_size.append(slide_no)
                reader.note(context, 'source_geometry_unverified',
                            'the size of this slide is not declared by its master page, so its blocks '
                            'carry no position')

            title = _page_title(context.blocks)
            state = 'usable'
            if any(issue.code in ('extraction_failure', 'render_failure') and issue.resolution == 'pending'
                   for issue in context.issues) or any(
                    issue.code == 'extraction_failure' and issue.resolution == 'pending'
                    for block in context.blocks for issue in block.issues):
                state = 'partial'
            if not context.blocks:
                state = 'partial'
                context.issues.append(ParseIssue(
                    code='extraction_failure', scope='page', page_id=page_id,
                    message=('this visible slide carries no object of its own; whatever it shows comes '
                             'from the master page and was not read'),
                ))
            pages.append(ParsedPage(
                page_id=page_id,
                order_index=len(pages),
                source_slide_no=slide_no,
                title=title,
                state=state,
                width_cm=context.slide_width_cm,
                height_cm=context.slide_height_cm,
                notes_present=has_notes,
                blocks=context.blocks,
                issues=context.issues,
            ))

        if not pages:
            reader.document_issues.append(ParseIssue(
                code='extraction_failure', scope='document',
                message=f'{len(slides)} slides, none of them visible: there is nothing a student could read',
            ))
        if pages_without_size:
            reader.document_issues.append(ParseIssue(
                code='source_geometry_unverified', scope='document',
                message=(f'{len(pages_without_size)} visible slides have no size from their master page; '
                         'their blocks carry no position'),
                detail={'source_slide_numbers': pages_without_size[:50],
                        'known_master_pages': sizes.known_masters},
            ))
        for hit in package.limit_hits:
            reader.document_issues.append(ParseIssue(
                code='extraction_failure', scope='document',
                message=f"package entry {hit['entry']!r} was not read: {hit['reason']}",
                detail=dict(hit),
            ))

        document = ParsedDocument(
            source_sha256=package.sha256,
            source_filename=path.name,
            source_size_bytes=package.size_bytes,
            reader_version=READER_VERSION,
            identity_version=IDENTITY_VERSION,
            config_hash=config_hash(options, limits),
            source_slide_count=len(slides),
            hidden_slide_numbers=tuple(hidden),
            notes_slide_count=notes_slides,
            pages=pages,
            assets=reader.assets,
            issues=reader.document_issues,
            fonts=tuple(sorted(set(_fonts(content)) | set(_fonts(styles_root)))),
            # Visible slides only; see _external_references.
            external_references=tuple(sorted(external_refs)),
            limit_hits=tuple(dict(hit) for hit in package.limit_hits),
        )
        return document.validate()


def _notes_carry_text(slide: ET.Element) -> bool:
    """Whether this slide has speaker notes with something written in them.

    The text is looked at and dropped on the spot. Only the answer yes or no
    leaves this function: the notes themselves never enter the result, and the
    renderer copy removes the notes node whatever the answer is.
    """
    notes = slide.find(Q['presentation'] + 'notes')
    if notes is None:
        return False
    return any(paragraph.strip() for paragraph in _paragraphs_including_notes(notes))


def _paragraphs_including_notes(element: ET.Element) -> list[str]:
    out: list[str] = []
    for node in element.iter():
        if prefixed_name(node.tag) in ('text:p', 'text:h'):
            out.append(''.join(node.itertext()))
    return out


def _page_title(blocks: list[ParsedBlock]) -> str:
    for block in blocks:
        if block.kind == 'heading' and block.content.strip():
            return block.content.strip().splitlines()[0]
    for block in blocks:
        if block.kind in ('text', 'code') and block.content.strip():
            return block.content.strip().splitlines()[0]
    return ''


def _check_reading_order(reader: _Reader, context: _PageContext, options: ReaderOptions) -> None:
    """Say whether the XML order agrees with the slide geometry.

    The XML order is the order we keep. When the geometry disagrees, or when a
    block has no geometry at all, the page is reported instead of being
    presented as a verified reading order.
    """
    if len(context.blocks) < 2:
        return
    if any(position is None for position in context.geometry):
        reader.note(context, 'source_geometry_unverified',
                    'at least one object has no position, so the reading order cannot be checked')
        return
    band = max(options.reading_order_band_cm, 0.01)
    keyed = [
        (round(position[1] / band), position[0], index)
        for index, position in enumerate(context.geometry)
    ]
    if [item[2] for item in sorted(keyed)] != list(range(len(keyed))):
        reader.note(context, 'source_geometry_unverified',
                    'the XML order of the objects does not match their position on the slide; '
                    'the reading order needs review')


def _check_coverage(reader: _Reader, context: _PageContext) -> None:
    """Every object we walked is either in a block or in the skipped list."""
    skipped = {entry['object_path'] for entry in context.skipped}
    missing = [path for path in context.visited if path not in context.attributed and path not in skipped]
    if missing:
        reader.note(context, 'extraction_failure',
                    f'{len(missing)} source objects on this slide were neither read nor reported',
                    detail={'object_paths': missing[:50]})


# --------------------------------------------------------------------------
# Walking one slide
# --------------------------------------------------------------------------

def _walk_container(reader: _Reader, context: _PageContext, container: ET.Element, slide_no: int,
                    *, trail: list[tuple[str, int]], group_path: tuple[str, ...]) -> None:
    """Walk the children of a slide or of a group, in document order."""
    counts: dict[str, int] = {}
    pending_vectors: list[tuple[str, ET.Element]] = []
    container_path = _object_path(context.page_index, trail) if trail else None

    def flush_vectors() -> None:
        """Turn the plain drawing shapes collected so far into one figure block.

        They are kept together because one connector is not a figure on its own,
        and they are never dropped: the block lists every member path and asks
        for a picture a human can check.

        Which picture depends on what holds them. Inside a group, the group is
        one object and rendering it shows every member. Loose on the slide,
        there is no single object that covers them, so the block asks for the
        whole page instead of pointing at the first member and calling that the
        figure.
        """
        if not pending_vectors:
            return
        paths = [path for path, _ in pending_vectors]
        elements = [element for _, element in pending_vectors]
        budget = TextBudget(reader.options)
        labels = [text for text in (_shape_text(element, budget) for element in elements) if text.strip()]
        whole_group = container_path is not None
        if whole_group:
            preview_note = 'a rendered preview of the group is needed for review'
        else:
            preview_note = (f'these {len(paths)} shapes are not held by one object, so the whole page '
                            'has to be shown for review')
        reader.make_block(
            context, kind='figure', content='', object_path=paths[0], element=elements[0],
            slide_no=slide_no, extraction_method='native_odf_vector_shapes',
            source_text='\n'.join(labels) or None,
            members=tuple(paths[1:]), group_path=group_path, completeness='incomplete',
            preview_required=True,
            preview_object_id=container_path if whole_group else None,
            preview_scope='object' if whole_group else 'page',
            discriminator=f'vectors:{len(paths)}', budget=budget,
            issues=(_issue('content_review',
                           f'{len(paths)} drawing shapes are kept as one figure; their connections and '
                           f'directions are not extracted and {preview_note}'),),
        )
        pending_vectors.clear()

    for child in container:
        tag = prefixed_name(child.tag)
        if tag == 'presentation:notes':
            # Never read, never counted as content.
            continue
        if tag in NON_CONTENT_TAGS:
            continue
        counts[tag] = counts.get(tag, 0) + 1
        child_trail = trail + [(tag, counts[tag])]
        path = _object_path(context.page_index, child_trail)
        context.visited.append(path)

        if tag == 'draw:g':
            # A group can hold several pictures; each one keeps its own block, so
            # a group never hides the pictures inside it.
            flush_vectors()
            inner_group_path = group_path + (_attr(child, 'draw', 'name') or f'group[{counts[tag]}]',)
            before = len(context.blocks)
            _walk_container(reader, context, child, slide_no, trail=child_trail, group_path=inner_group_path)
            if len(context.blocks) > before:
                context.attributed.add(path)
            else:
                context.skipped.append({'object_path': path, 'reason': 'the group holds nothing we could read'})
                reader.note(context, 'extraction_failure',
                            f'group {path} holds no object this reader could read')
            continue

        if tag in VECTOR_TAGS:
            budget = TextBudget(reader.options)
            text = _shape_text(child, budget)
            if text.strip():
                # A labelled shape is native text and is kept as text.
                reader.make_block(
                    context, kind='text', content=text, object_path=path, element=child,
                    slide_no=slide_no, extraction_method='native_odf_shape_text',
                    group_path=group_path, completeness='complete', budget=budget,
                )
            else:
                pending_vectors.append((path, child))
            continue

        if tag == 'draw:frame':
            flush_vectors()
            _read_frame(reader, context, child, slide_no, path, group_path)
            continue

        if tag == 'draw:page-thumbnail':
            flush_vectors()
            reader.make_block(
                context, kind='navigation', content='', object_path=path, element=child,
                slide_no=slide_no, extraction_method='native_odf_page_thumbnail',
                group_path=group_path, completeness='complete',
            )
            continue

        flush_vectors()
        context.skipped.append({'object_path': path, 'reason': f'unclassified object {tag}'})
        reader.note(context, 'extraction_failure',
                    f'object {tag} at {path} was not read by this reader',
                    detail={'object_path': path, 'tag': tag})

    # A group that is only a drawing gets one preview request for the group.
    flush_vectors()


def _frame_child(frame: ET.Element, wanted: str) -> ET.Element | None:
    for child in frame:
        if prefixed_name(child.tag) == wanted:
            return child
    return None


def _read_frame(reader: _Reader, context: _PageContext, frame: ET.Element, slide_no: int,
                path: str, group_path: tuple[str, ...]) -> None:
    """One draw:frame: an embedded object, a table, a picture or a text body.

    The embedded object is looked at first on purpose. A frame that holds a
    sheet or a drawing also holds a replacement picture that the office suite
    drew for it, and reading that picture first is how the structure of the
    object gets lost. The replacement is kept as an extra asset, not as the
    content.
    """
    presentation_class = _attr(frame, 'presentation', 'class')
    image = _frame_child(frame, 'draw:image')
    table = frame.find(f".//{Q['table']}table")
    embedded = _frame_child(frame, 'draw:object')
    if embedded is None:
        embedded = _frame_child(frame, 'draw:object-ole')
    text_box = _frame_child(frame, 'draw:text-box')

    if embedded is not None:
        _read_embedded_object(reader, context, frame, embedded, slide_no, path, group_path,
                              replacement=image)
        return
    if table is not None:
        _read_native_table(reader, context, frame, table, slide_no, path, group_path)
        return
    if image is not None:
        _read_image_frame(reader, context, frame, image, slide_no, path, group_path)
        return
    if text_box is not None:
        budget = TextBudget(reader.options)
        text = _shape_text(text_box, budget)
        if not text.strip():
            context.skipped.append({'object_path': path, 'reason': 'empty text placeholder'})
            return
        kind = 'text'
        if presentation_class in HEADING_CLASSES:
            kind = 'heading'
        elif presentation_class in NAVIGATION_CLASSES:
            kind = 'navigation'
        reader.make_block(
            context, kind=kind, content=text, object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_text_box', group_path=group_path, completeness='complete',
            budget=budget,
        )
        return

    unsupported = next((prefixed_name(child.tag) for child in frame
                        if prefixed_name(child.tag) in ('draw:plugin', 'draw:applet', 'draw:floating-frame')), None)
    reader.make_block(
        context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
        extraction_method='native_odf_unreadable_frame', group_path=group_path,
        completeness='incomplete', preview_required=True,
        issues=(_issue('extraction_failure',
                       f'frame {unsupported or "with no content this reader knows"} was not read; '
                       'a rendered preview is needed to see what it holds'),),
    )


def _read_image_frame(reader: _Reader, context: _PageContext, frame: ET.Element, image: ET.Element,
                      slide_no: int, path: str, group_path: tuple[str, ...]) -> None:
    href = image.attrib.get(Q['xlink'] + 'href') or ''
    caption = _shape_text(frame, TextBudget(reader.options))
    title = frame.find(Q['svg'] + 'title')
    description = frame.find(Q['svg'] + 'desc')
    labels = [element.text for element in (title, description) if element is not None and element.text]
    source_text = '\n'.join(labels + ([caption] if caption.strip() else [])) or None

    scheme = external_scheme(href)
    if scheme:
        reader.make_block(
            context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_external_image', source_text=source_text,
            group_path=group_path, completeness='incomplete', preview_required=True,
            issues=(_issue('extraction_failure',
                           f'the picture is referenced over {scheme} and is not fetched; '
                           'the slide may show nothing there'),),
        )
        return

    entry = package_relative_href(href)
    if entry is None:
        reader.make_block(
            context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_image', source_text=source_text, group_path=group_path,
            completeness='incomplete', preview_required=True,
            issues=(_issue('extraction_failure',
                           f'the picture href {href!r} does not name an entry inside the package'),),
        )
        return
    if not reader.package.has(entry):
        reader.make_block(
            context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_image', source_text=source_text, group_path=group_path,
            completeness='incomplete', preview_required=True, package_entries=(entry,),
            issues=(_issue('extraction_failure',
                           f'the picture entry {entry!r} is missing from the package or was over a limit'),),
        )
        return

    asset = reader.add_asset(entry, kind='embedded_image', page_id=context.page_id, source_object_id=path)
    if asset is None:
        reader.make_block(
            context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_image', source_text=source_text, group_path=group_path,
            completeness='incomplete', preview_required=True, package_entries=(entry,),
            issues=(_issue('extraction_failure',
                           f'the picture entry {entry!r} was not stored: an asset limit was reached'),),
        )
        return
    reader.make_block(
        context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
        extraction_method='native_odf_image', source_text=source_text, group_path=group_path,
        asset_ids=(asset.asset_id,), completeness='complete', package_entries=(entry,),
    )


def _read_native_table(reader: _Reader, context: _PageContext, frame: ET.Element, table: ET.Element,
                       slide_no: int, path: str, group_path: tuple[str, ...]) -> None:
    budget = TextBudget(reader.options)
    grid = read_table(table, max_cells=reader.options.max_table_cells,
                      max_rows=reader.options.max_table_rows, budget=budget)
    issues = []
    completeness = 'complete'
    if grid.truncated:
        completeness = 'incomplete'
        issues.append(_issue(
            'extraction_failure',
            f'{grid.truncation_reason}: {grid.read_cells} of {grid.declared_cells} declared cells '
            f'were read',
            detail={'read_cells': grid.read_cells, 'declared_cells': grid.declared_cells,
                    'reason': grid.truncation_reason},
        ))
    reader.make_block(
        context, kind='table', content=grid.as_text(), object_path=path, element=frame,
        slide_no=slide_no, extraction_method='native_odf_table', group_path=group_path,
        completeness=completeness, table_data=grid.to_dict(), issues=tuple(issues), budget=budget,
    )


def _replacement_asset(reader: _Reader, context: _PageContext, replacement: ET.Element | None,
                       path: str) -> tuple[tuple[str, ...], list[ParseIssue]]:
    """The picture the office suite drew for an embedded object, when it is usable.

    It is an extra view of the object, never its content. A replacement that is
    not a real raster picture — a GDI metafile, for instance — is not stored as
    an image; the block keeps asking for a rendered preview instead.
    """
    if replacement is None:
        return (), []
    href = replacement.attrib.get(Q['xlink'] + 'href') or ''
    entry = package_relative_href(href)
    if entry is None or not reader.package.has(entry):
        return (), [_issue('content_review',
                           'the object has a replacement picture we could not read; '
                           'a rendered preview is needed')]
    media = reader.package.media_type(entry) or _guess_mime(entry)
    if not media.startswith('image/'):
        return (), [_issue('content_review',
                           f'the replacement picture of this object is a {media or "unknown"} file, '
                           'which is not a picture we can show; a rendered preview is needed')]
    asset = reader.add_asset(entry, kind='embedded_image', page_id=context.page_id, source_object_id=path,
                             mime=media)
    if asset is None:
        return (), [_issue('content_review', 'the replacement picture of this object was not stored')]
    asset.notes = ('replacement picture of an embedded object, not the object itself',)
    return (asset.asset_id,), []


def _read_embedded_object(reader: _Reader, context: _PageContext, frame: ET.Element, embedded: ET.Element,
                          slide_no: int, path: str, group_path: tuple[str, ...],
                          replacement: ET.Element | None = None) -> None:
    href = embedded.attrib.get(Q['xlink'] + 'href') or ''
    inline_math = embedded.find(f".//{Q['math']}math")
    if inline_math is not None and not href:
        _read_inline_formula(reader, context, frame, inline_math, slide_no, path, group_path)
        return

    extra_assets, extra_issues = _replacement_asset(reader, context, replacement, path)
    scheme = external_scheme(href)
    folder = package_relative_href(href)
    if scheme or folder is None:
        reader.make_block(
            context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_embedded_object', group_path=group_path,
            completeness='incomplete', preview_required=True, asset_ids=extra_assets,
            issues=(_issue('extraction_failure',
                           f'the embedded object href {href!r} does not name an entry inside the package'),
                    *extra_issues),
        )
        return

    media = reader.package.media_type(folder)
    object_content = f'{folder}/content.xml'
    if media == FORMULA_MIME or (not media and reader.package.has(object_content)
                                 and _looks_like_formula(reader, object_content)):
        _read_formula_object(reader, context, frame, slide_no, path, group_path, folder,
                             extra_assets=extra_assets, extra_issues=extra_issues)
        return
    if media.startswith(SPREADSHEET_MIME_PREFIX):
        _read_embedded_spreadsheet(reader, context, frame, slide_no, path, group_path, folder,
                                   extra_assets=extra_assets, extra_issues=extra_issues)
        return
    if media.startswith(GRAPHICS_MIME_PREFIX):
        _read_embedded_graphics(reader, context, frame, slide_no, path, group_path, folder,
                                extra_assets=extra_assets, extra_issues=extra_issues)
        return

    reader.make_block(
        context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
        extraction_method='native_odf_embedded_object', group_path=group_path,
        completeness='incomplete', preview_required=True, package_entries=(folder,),
        asset_ids=extra_assets,
        issues=(_issue('extraction_failure',
                       f'embedded object of type {media or "unknown"} was not read; '
                       'a rendered preview is needed to see what it holds'),
                *extra_issues),
    )


def _looks_like_formula(reader: _Reader, entry: str) -> bool:
    try:
        head = reader.package.read(entry)[:4096]
    except OdfPackageError:
        return False
    return b'math' in head.lower()


def _read_inline_formula(reader: _Reader, context: _PageContext, frame: ET.Element, math: ET.Element,
                         slide_no: int, path: str, group_path: tuple[str, ...]) -> None:
    payload = ET.tostring(math, encoding='utf-8')
    annotation = _starmath_annotation(payload)
    asset = ParsedAsset(
        asset_id=reader.asset_id(f'{path}#inline-math'),
        kind='formula_source', mime='application/mathml+xml', sha256=sha256_bytes(payload),
        size_bytes=len(payload), data=payload, source_object_id=path, page_id=context.page_id,
    )
    reader.assets.append(asset)
    reader.make_block(
        context, kind='formula', content=annotation or '', object_path=path, element=frame,
        slide_no=slide_no, extraction_method='native_odf_inline_mathml', group_path=group_path,
        source_text=annotation, asset_ids=(asset.asset_id,), completeness='unknown',
        preview_required=True,
        issues=(_issue('content_review',
                       'the formula object is kept as it is; its own StarMath annotation is the only text '
                       'and it is not a LaTeX transcription'),),
    )


def _starmath_annotation(payload: bytes) -> str | None:
    """The formula's own annotation text, when the object carries one.

    This is text the author wrote, read back unchanged. It is not LaTeX and the
    block never claims that it is.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    for element in root.iter():
        if element.tag.split('}')[-1] == 'annotation' and element.text:
            return element.text
    return None


def _read_formula_object(reader: _Reader, context: _PageContext, frame: ET.Element, slide_no: int,
                         path: str, group_path: tuple[str, ...], folder: str, *,
                         extra_assets: tuple[str, ...] = (), extra_issues: list | None = None) -> None:
    extra_issues = list(extra_issues or [])
    entry = f'{folder}/content.xml'
    if not reader.package.has(entry):
        reader.make_block(
            context, kind='formula', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_formula_object', group_path=group_path,
            completeness='incomplete', preview_required=True, package_entries=(folder,),
            asset_ids=extra_assets,
            issues=(_issue('extraction_failure', f'the formula object {folder!r} has no readable content.xml'),
                    *extra_issues),
        )
        return
    payload = reader.package.read(entry)
    annotation = _starmath_annotation(payload)
    asset = reader.add_asset(
        entry, kind='formula_source', page_id=context.page_id, source_object_id=path,
        mime='application/mathml+xml' if b'math' in payload[:4096].lower() else 'application/xml',
    )
    reader.make_block(
        context, kind='formula', content=annotation or '', object_path=path, element=frame,
        slide_no=slide_no, extraction_method='native_odf_formula_object', group_path=group_path,
        source_text=annotation, asset_ids=((asset.asset_id,) if asset else ()) + extra_assets,
        completeness='unknown', preview_required=True, package_entries=(folder,),
        issues=(_issue('content_review',
                       'the formula object is kept as it is, with its own StarMath annotation when it has one; '
                       'it is not converted to LaTeX and needs review'),
                *extra_issues),
    )


def _read_embedded_spreadsheet(reader: _Reader, context: _PageContext, frame: ET.Element, slide_no: int,
                               path: str, group_path: tuple[str, ...], folder: str, *,
                               extra_assets: tuple[str, ...] = (), extra_issues: list | None = None) -> None:
    extra_issues = list(extra_issues or [])
    entry = f'{folder}/content.xml'
    failure = None
    root = None
    if not reader.package.has(entry):
        failure = f'the embedded sheet {folder!r} has no readable content.xml'
    else:
        try:
            root = reader.package.xml(entry)
        except OdfPackageError as exc:
            failure = f'the embedded sheet {folder!r} could not be parsed: {exc}'
    tables = list(root.iter(Q['table'] + 'table')) if root is not None else []
    if failure is None and not tables:
        failure = f'the embedded sheet {folder!r} holds no table'
    if failure is not None:
        reader.make_block(
            context, kind='table', content='', object_path=path, element=frame, slide_no=slide_no,
            extraction_method='native_odf_embedded_spreadsheet', group_path=group_path,
            completeness='incomplete', preview_required=True, package_entries=(folder,),
            asset_ids=extra_assets,
            issues=(_issue('extraction_failure', failure), *extra_issues),
        )
        return
    for position, table in enumerate(tables, start=1):
        budget = TextBudget(reader.options)
        grid = read_table(table, max_cells=reader.options.max_table_cells,
                          max_rows=reader.options.max_table_rows, budget=budget)
        issues = list(extra_issues)
        completeness = 'complete'
        if grid.truncated:
            completeness = 'incomplete'
            issues.append(_issue(
                'extraction_failure',
                f'the embedded sheet stopped because {grid.truncation_reason}: {grid.read_cells} of '
                f'{grid.declared_cells} declared cells were read',
                detail={'read_cells': grid.read_cells, 'declared_cells': grid.declared_cells,
                        'reason': grid.truncation_reason},
            ))
        if not grid.rows:
            completeness = 'incomplete'
            issues.append(_issue('extraction_failure',
                                 f'the embedded sheet {folder!r} has a table with no cell we could read'))
        reader.make_block(
            context, kind='table', content=grid.as_text(), object_path=path, element=frame,
            slide_no=slide_no, extraction_method='native_odf_embedded_spreadsheet',
            group_path=group_path, completeness=completeness, table_data=grid.to_dict(),
            package_entries=(folder,), asset_ids=extra_assets if position == 1 else (),
            issues=tuple(issues), budget=budget,
            discriminator=f'sheet:{position}' if position > 1 else '',
        )


def _read_embedded_graphics(reader: _Reader, context: _PageContext, frame: ET.Element, slide_no: int,
                            path: str, group_path: tuple[str, ...], folder: str, *,
                            extra_assets: tuple[str, ...] = (), extra_issues: list | None = None) -> None:
    """An ODG object: keep its native labels and every picture inside it.

    The drawing's relations and directions are not extracted. The block says so
    and asks for a rendered preview instead of claiming a reading of the figure.
    """
    entry = f'{folder}/content.xml'
    labels: list[str] = []
    asset_ids: list[str] = list(extra_assets)
    entries: list[str] = [folder]
    issues = list(extra_issues or [])
    issues.append(_issue('content_review',
                         'a vector drawing: its native labels are kept, its connections and directions are '
                         'not extracted, and a rendered preview is needed for review'))
    budget = TextBudget(reader.options)
    if reader.package.has(entry):
        try:
            root = reader.package.xml(entry)
        except OdfPackageError as exc:
            issues.append(_issue('extraction_failure', f'the drawing {folder!r} could not be parsed: {exc}'))
            root = None
        if root is not None:
            for paragraph in _paragraphs(root, budget):
                if paragraph.strip() and paragraph not in labels:
                    labels.append(paragraph)
            # A drawing can hold its own pictures. Each one is kept, so an
            # object never hides the images inside it.
            for image in root.iter(Q['draw'] + 'image'):
                href = image.attrib.get(Q['xlink'] + 'href') or ''
                if external_scheme(href):
                    issues.append(_issue('extraction_failure',
                                         f'a picture inside the drawing is referenced over the network: {href}'))
                    continue
                relative = package_relative_href(href)
                if relative is None:
                    issues.append(_issue('extraction_failure',
                                         f'a picture inside the drawing has an href we do not follow: {href!r}'))
                    continue
                candidate = relative if reader.package.has(relative) else posixpath.normpath(
                    posixpath.join(folder, relative))
                if not reader.package.has(candidate):
                    issues.append(_issue('extraction_failure',
                                         f'a picture inside the drawing is missing from the package: {relative!r}'))
                    continue
                asset = reader.add_asset(candidate, kind='embedded_image', page_id=context.page_id,
                                         source_object_id=path)
                if asset is None:
                    issues.append(_issue('extraction_failure',
                                         f'a picture inside the drawing was not stored: {candidate!r}'))
                    continue
                asset_ids.append(asset.asset_id)
                entries.append(candidate)
    else:
        issues.append(_issue('extraction_failure', f'the drawing {folder!r} has no readable content.xml'))

    reader.make_block(
        context, kind='figure', content='', object_path=path, element=frame, slide_no=slide_no,
        extraction_method='native_odf_embedded_graphics', group_path=group_path,
        source_text='\n'.join(labels) or None, asset_ids=tuple(asset_ids),
        completeness='incomplete', preview_required=True, package_entries=tuple(entries),
        issues=tuple(issues), budget=budget,
    )
