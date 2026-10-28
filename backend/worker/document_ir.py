"""The parse result shared by the readers, the renderer client and the tests.

M2-A production module. It holds no database session, no ORM object, no
embedding and no student identity: an adapter returns this structure, and a
later package decides what to store, index or publish.

Two rules are enforced here rather than left to each caller:

* a hidden slide never becomes a page, and speaker notes text is never carried
  at all, so no report or student-facing structure can leak it;
* content is stored exactly as it was read. Nothing in this module strips,
  collapses or re-wraps a character of source text.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

SCHEMA_VERSION = 'm2a-parsed-document-1'

# Fixed vocabularies from the roadmap. A value outside them is a programming
# error and fails loudly instead of reaching the database.
BLOCK_KINDS = ('heading', 'text', 'code', 'formula', 'table', 'figure', 'navigation')
CONTENT_FORMATS = ('plain', 'markdown', 'latex')
COMPLETENESS = ('complete', 'incomplete', 'unknown')
PAGE_STATES = ('usable', 'partial', 'unavailable')
ISSUE_RESOLUTIONS = ('pending', 'confirmed', 'ignored')
# The reusable codes from the roadmap, plus the two it adds for M2. "document"
# scope is an addition of this module: a ZIP limit or a failed render belongs to
# the file, not to one page, and must not be hidden on an unrelated page.
ISSUE_CODES = (
    'extraction_failure',
    'render_failure',
    'content_review',
    'protocol_failure',
    'budget_exhausted',
    'source_geometry_unverified',
    'preview_mapping_failed',
    'source_identity_conflict',
)
ISSUE_SCOPES = ('document', 'page', 'block')

# Asset kinds. embedded_image and formula_source come straight out of the source
# file; page_preview and figure_preview are produced by the renderer.
ASSET_KINDS = ('embedded_image', 'formula_source', 'page_preview', 'figure_preview', 'preview_pdf')
ACCESS_CLASSES = ('teacher_original', 'student_visible')


class IRError(ValueError):
    """An invariant of the parse result was broken."""


def stable_id(identity_version: str, file_sha256: str, *parts: str) -> str:
    """Identity from the file hash, the native object path and the algorithm version.

    The same file parsed again gives the same id. A different file gives
    different ids: nothing here lets a new upload inherit an old identity.
    """
    payload = '\x1f'.join([identity_version, file_sha256, *parts])
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


@dataclass(frozen=True)
class ParseIssue:
    """One problem worth a human decision, or one gap we refuse to hide."""

    code: str
    scope: str
    message: str
    page_id: str | None = None
    block_id: str | None = None
    resolution: str = 'pending'
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in ISSUE_CODES:
            raise IRError(f'unknown issue code {self.code!r}')
        if self.scope not in ISSUE_SCOPES:
            raise IRError(f'unknown issue scope {self.scope!r}')
        if self.resolution not in ISSUE_RESOLUTIONS:
            raise IRError(f'unknown issue resolution {self.resolution!r}')
        if self.scope == 'block' and not self.block_id:
            raise IRError('a block issue needs a block_id')
        if self.scope == 'page' and not self.page_id:
            raise IRError('a page issue needs a page_id')

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'scope': self.scope,
            'message': self.message,
            'page_id': self.page_id,
            'block_id': self.block_id,
            'resolution': self.resolution,
            'detail': dict(self.detail),
        }


@dataclass(frozen=True)
class SourceRef:
    """Where a block came from, in terms of the source file itself."""

    file_sha256: str
    source_slide_no: int
    native_object_ids: tuple[str, ...] = ()
    extraction_method: str = 'native_odf'
    identity_version: str = ''
    bbox: tuple[float, float, float, float] | None = None
    bbox_basis: str | None = None
    native_draw_names: tuple[str, ...] = ()
    package_entries: tuple[str, ...] = ()
    format: str = 'odp'
    legacy_ref: dict | None = None
    content_origin: str = 'native'

    def to_dict(self) -> dict:
        return {
            'format': self.format,
            'file_sha256': self.file_sha256,
            'source_slide_no': self.source_slide_no,
            'native_object_ids': list(self.native_object_ids),
            'native_draw_names': list(self.native_draw_names),
            'package_entries': list(self.package_entries),
            'extraction_method': self.extraction_method,
            'identity_version': self.identity_version,
            'bbox': list(self.bbox) if self.bbox else None,
            'bbox_basis': self.bbox_basis,
            'legacy_ref': self.legacy_ref,
            'content_origin': self.content_origin,
        }


@dataclass
class ParsedAsset:
    """A file that belongs to the document: an original image, a formula object,
    or a preview produced by the renderer.

    ``data`` stays out of to_dict(): reports and logs must not carry image bytes.
    """

    asset_id: str
    kind: str
    mime: str
    sha256: str
    size_bytes: int
    data: bytes | None = field(default=None, repr=False)
    source_entry: str | None = None
    source_object_id: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    access_class: str = 'teacher_original'
    page_id: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ASSET_KINDS:
            raise IRError(f'unknown asset kind {self.kind!r}')
        if self.access_class not in ACCESS_CLASSES:
            raise IRError(f'unknown access class {self.access_class!r}')

    def to_dict(self) -> dict:
        return {
            'asset_id': self.asset_id,
            'kind': self.kind,
            'mime': self.mime,
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'source_entry': self.source_entry,
            'source_object_id': self.source_object_id,
            'width_px': self.width_px,
            'height_px': self.height_px,
            'access_class': self.access_class,
            'page_id': self.page_id,
            'notes': list(self.notes),
        }


@dataclass
class ParsedBlock:
    """One reading and source unit on a visible page."""

    block_id: str
    page_id: str
    order_index: int
    kind: str
    content: str
    source_ref: SourceRef
    content_format: str = 'plain'
    source_text: str | None = None
    section_path: tuple[str, ...] = ()
    group_path: tuple[str, ...] = ()
    asset_ids: tuple[str, ...] = ()
    source_completeness: str = 'unknown'
    # preview_required says a human needs a rendered picture before this block
    # can be reviewed. preview_object_id names the object to render, and
    # preview_scope says whether that object covers the whole block: 'page'
    # means the members cannot be rendered together on their own and the whole
    # slide has to be shown instead.
    preview_required: bool = False
    preview_object_id: str | None = None
    preview_scope: str = 'object'
    # The authoritative table structure, including repeated and merged cells.
    # ``content`` is only a readable rendering of it.
    table_data: dict | None = None
    model_candidate: str | None = None
    # The reader's class of a picture (MinerU sub_type: flowchart, text_image,
    # natural_image, a chart type), when it gives one.
    figure_type: str | None = None
    # A caption of the picture written by a model after reading
    # (picture_captions.py), and where it came from (model, prompt, image hash).
    caption: str | None = None
    caption_source: dict | None = None
    issues: list[ParseIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in BLOCK_KINDS:
            raise IRError(f'unknown block kind {self.kind!r}')
        if self.content_format not in CONTENT_FORMATS:
            raise IRError(f'unknown content format {self.content_format!r}')
        if self.source_completeness not in COMPLETENESS:
            raise IRError(f'unknown source completeness {self.source_completeness!r}')
        if self.preview_scope not in ('object', 'page'):
            raise IRError(f'unknown preview scope {self.preview_scope!r}')
        if self.preview_scope == 'page' and self.preview_object_id is not None:
            raise IRError('a page-wide preview cannot name a single object')
        if self.kind == 'formula' and self.content_format == 'latex' and self.source_ref.format == 'odp':
            # M2-A never converts a formula object. A latex format here would
            # claim a transcription we did not make.
            raise IRError('a formula read from an ODP object cannot be stored as latex in M2-A')

    def to_dict(self) -> dict:
        return {
            'block_id': self.block_id,
            'page_id': self.page_id,
            'order_index': self.order_index,
            'kind': self.kind,
            'content': self.content,
            'content_format': self.content_format,
            'source_text': self.source_text,
            'section_path': list(self.section_path),
            'group_path': list(self.group_path),
            'asset_ids': list(self.asset_ids),
            'source_ref': self.source_ref.to_dict(),
            'source_completeness': self.source_completeness,
            'preview_required': self.preview_required,
            'preview_object_id': self.preview_object_id,
            'preview_scope': self.preview_scope,
            'table_data': self.table_data,
            'model_candidate': self.model_candidate,
            'figure_type': self.figure_type,
            'caption': self.caption,
            'caption_source': self.caption_source,
            'issues': [issue.to_dict() for issue in self.issues],
        }


@dataclass
class ParsedPage:
    """One visible slide. Hidden slides never reach this structure."""

    page_id: str
    order_index: int
    source_slide_no: int
    title: str = ''
    state: str = 'usable'
    preview_page_no: int | None = None
    preview_mapping_check: str = 'not_attempted'
    preview_asset_id: str | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    notes_present: bool = False
    blocks: list[ParsedBlock] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in PAGE_STATES:
            raise IRError(f'unknown page state {self.state!r}')

    def to_dict(self) -> dict:
        return {
            'page_id': self.page_id,
            'order_index': self.order_index,
            'source_slide_no': self.source_slide_no,
            'title': self.title,
            'state': self.state,
            # The source slide number and the preview page number are separate
            # fields on purpose; equal counts are not a mapping proof.
            'preview_page_no': self.preview_page_no,
            'preview_mapping_check': self.preview_mapping_check,
            'preview_asset_id': self.preview_asset_id,
            'width_cm': self.width_cm,
            'height_cm': self.height_cm,
            'notes_present': self.notes_present,
            'blocks': [block.to_dict() for block in self.blocks],
            'issues': [issue.to_dict() for issue in self.issues],
        }


@dataclass
class ParsedDocument:
    """Everything one parse of one source file produced."""

    source_sha256: str
    source_filename: str
    source_size_bytes: int
    reader_version: str
    identity_version: str
    config_hash: str
    schema_version: str = SCHEMA_VERSION
    source_slide_count: int = 0
    hidden_slide_numbers: tuple[int, ...] = ()
    notes_slide_count: int = 0
    pages: list[ParsedPage] = field(default_factory=list)
    assets: list[ParsedAsset] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    fonts: tuple[str, ...] = ()
    external_references: tuple[str, ...] = ()
    limit_hits: tuple[dict, ...] = ()

    # -- reading helpers ---------------------------------------------------

    def all_blocks(self):
        for page in self.pages:
            for block in page.blocks:
                yield page, block

    def all_issues(self) -> list[ParseIssue]:
        out = list(self.issues)
        for page in self.pages:
            out.extend(page.issues)
            for block in page.blocks:
                out.extend(block.issues)
        return out

    def asset_by_id(self, asset_id: str) -> ParsedAsset | None:
        return next((asset for asset in self.assets if asset.asset_id == asset_id), None)

    def block_by_id(self, block_id: str) -> ParsedBlock | None:
        return next((block for _, block in self.all_blocks() if block.block_id == block_id), None)

    @property
    def visible_slide_numbers(self) -> tuple[int, ...]:
        return tuple(page.source_slide_no for page in self.pages)

    def parse_quality(self) -> str:
        """complete or partial. Never failed: a document that cannot be read at
        all raises before an IR exists."""
        blocking = [
            issue for issue in self.all_issues()
            if issue.resolution == 'pending'
            and issue.code in ('extraction_failure', 'render_failure', 'preview_mapping_failed',
                               'source_identity_conflict', 'protocol_failure', 'budget_exhausted')
        ]
        if (blocking or any(page.state != 'usable' for page in self.pages)
                or any(block.source_completeness != 'complete' for _, block in self.all_blocks())):
            return 'partial'
        return 'complete'

    def with_pages(self, pages: list[ParsedPage]) -> 'ParsedDocument':
        return replace(self, pages=pages)

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'reader_version': self.reader_version,
            'identity_version': self.identity_version,
            'config_hash': self.config_hash,
            'source_filename': self.source_filename,
            'source_sha256': self.source_sha256,
            'source_size_bytes': self.source_size_bytes,
            'source_slide_count': self.source_slide_count,
            'visible_slide_count': len(self.pages),
            'hidden_slide_count': len(self.hidden_slide_numbers),
            'hidden_slide_numbers': list(self.hidden_slide_numbers),
            # A count only. The notes text itself is never read into the IR.
            'notes_slide_count': self.notes_slide_count,
            'fonts': list(self.fonts),
            'external_references': list(self.external_references),
            'limit_hits': [dict(hit) for hit in self.limit_hits],
            'parse_quality': self.parse_quality(),
            'pages': [page.to_dict() for page in self.pages],
            'assets': [asset.to_dict() for asset in self.assets],
            'issues': [issue.to_dict() for issue in self.issues],
            'totals': self.totals(),
        }

    def totals(self) -> dict:
        kinds: dict[str, int] = {}
        for _, block in self.all_blocks():
            kinds[block.kind] = kinds.get(block.kind, 0) + 1
        return {
            'pages': len(self.pages),
            'blocks': sum(len(page.blocks) for page in self.pages),
            'blocks_by_kind': kinds,
            'assets': len(self.assets),
            'issues': len(self.all_issues()),
            'blocks_needing_preview': sum(1 for _, b in self.all_blocks() if b.preview_required),
        }

    def validate(self) -> 'ParsedDocument':
        """Check the invariants a caller is allowed to rely on."""
        seen_pages: set[str] = set()
        seen_blocks: set[str] = set()
        asset_ids = {asset.asset_id for asset in self.assets}
        if len(asset_ids) != len(self.assets):
            raise IRError('duplicate asset id in the document')
        for position, page in enumerate(self.pages):
            if page.page_id in seen_pages:
                raise IRError(f'duplicate page id {page.page_id}')
            seen_pages.add(page.page_id)
            if page.order_index != position:
                raise IRError(f'page order_index {page.order_index} is not its position {position}')
            if page.source_slide_no in self.hidden_slide_numbers:
                raise IRError(f'slide {page.source_slide_no} is hidden and cannot be a page')
            for index, block in enumerate(page.blocks):
                if block.block_id in seen_blocks:
                    raise IRError(f'duplicate block id {block.block_id}')
                seen_blocks.add(block.block_id)
                if block.page_id != page.page_id:
                    raise IRError(f'block {block.block_id} does not belong to its page')
                if block.order_index != index:
                    raise IRError(f'block order_index {block.order_index} is not its position {index}')
                if block.source_ref.source_slide_no != page.source_slide_no:
                    raise IRError(f'block {block.block_id} points at another slide')
                unknown = [a for a in block.asset_ids if a not in asset_ids]
                if unknown:
                    raise IRError(f'block {block.block_id} points at unknown assets {unknown}')
        for asset in self.assets:
            if asset.page_id is not None and asset.page_id not in seen_pages:
                raise IRError(f'asset {asset.asset_id} points at a page that is not in the document')
        for issue in self.all_issues():
            if issue.page_id is not None and issue.page_id not in seen_pages:
                raise IRError(f'issue {issue.code} points at a page that is not in the document')
            if issue.block_id is not None and issue.block_id not in seen_blocks:
                raise IRError(f'issue {issue.code} points at a block that is not in the document')
        return self
