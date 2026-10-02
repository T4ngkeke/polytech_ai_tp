"""Worker side of the slide renderer: build a task, run it, check what came back.

M2-A production module. The renderer itself is a separate component with no
network, no database and no model credentials (see backend/renderer/). This
module never starts it directly with a hard-coded docker command line and never
mounts a docker socket: the command is configuration, and everything the
component hands back is validated before it is used.

What is checked on the way back:

* the result belongs to the task we created;
* every output path stays inside the task's own output directory, is not a
  symlink and is not absolute;
* the bytes start with the magic of the type the renderer claims, and the hash
  matches;
* the page count is read from the PDF here, not taken from the renderer's word.

Equal page counts are never treated as a proof of the page mapping. The mapping
is checked against text anchors from the source, and anything unproven is
reported for a human to look at.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from backend.worker.document_ir import ParsedAsset, ParsedDocument, ParseIssue, stable_id
from backend.worker.odp_sanitize import SanitizedCopy, write_object_preview_copy, write_render_copy

RENDERER_PROTOCOL = 1
TASK_FILE = 'task.json'
RESULT_FILE = 'result.json'
INPUT_DIR = 'in'
OUTPUT_DIR = 'out'

OUTPUT_KINDS = ('preview_pdf', 'page_png', 'object_png')
MAGIC = {
    'preview_pdf': b'%PDF-',
    'page_png': b'\x89PNG\r\n\x1a\n',
    'object_png': b'\x89PNG\r\n\x1a\n',
}
# The renderer is started with this environment and nothing else. No database
# URL, no model key, no token from the worker's own environment.
RENDERER_ENV_ALLOWLIST = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ')

ANCHOR_MIN_CHARS = 12
ANCHOR_COMPARE_CHARS = 80


class RendererUnavailable(RuntimeError):
    """No renderer is configured, or the configured one could not be started."""


class RenderFailed(RuntimeError):
    """The renderer ran and reported a failure, or produced nothing usable."""


class RenderValidationError(RuntimeError):
    """The renderer's output did not pass validation and is not used at all."""


@dataclass(frozen=True)
class RenderSettings:
    """Renderer configuration, read from the environment of the worker process.

    Nothing here defaults to a working renderer: with no command configured the
    caller gets RendererUnavailable and can record the gap, instead of the
    worker quietly inventing a docker command line.
    """

    command: tuple[str, ...] = ()
    timeout_seconds: int = 180
    image: str = ''
    dpi: int = 110
    want_page_images: bool = False

    @classmethod
    def from_env(cls, environ: dict | None = None) -> 'RenderSettings':
        env = environ if environ is not None else os.environ
        raw = (env.get('SLIDE_RENDERER_COMMAND') or '').strip()
        return cls(
            command=tuple(shlex.split(raw)) if raw else (),
            timeout_seconds=int(env.get('SLIDE_RENDERER_TIMEOUT_SECONDS') or 180),
            image=(env.get('SLIDE_RENDERER_IMAGE') or '').strip(),
            dpi=int(env.get('SLIDE_RENDERER_DPI') or 110),
            want_page_images=(env.get('SLIDE_RENDERER_PAGE_IMAGES') or '0') == '1',
        )

    def as_report(self) -> dict:
        """What went into the run, for the acceptance report."""
        return {
            'configured': bool(self.command),
            'command': list(self.command),
            'image': self.image,
            'timeout_seconds': self.timeout_seconds,
            'dpi': self.dpi,
            'page_images': self.want_page_images,
            'protocol': RENDERER_PROTOCOL,
        }


@dataclass
class RenderTask:
    """One prepared task directory. It is the only thing the renderer may see."""

    task_id: str
    kind: str
    task_dir: Path
    input_name: str
    expected_page_count: int
    copy: SanitizedCopy
    source_slide_numbers: tuple[int, ...] = ()
    object_paths: tuple[str, ...] = ()
    settings: RenderSettings = field(default_factory=RenderSettings)

    @property
    def input_path(self) -> Path:
        return self.task_dir / INPUT_DIR / self.input_name

    @property
    def output_dir(self) -> Path:
        return self.task_dir / OUTPUT_DIR

    def to_dict(self) -> dict:
        return {
            'protocol': RENDERER_PROTOCOL,
            'task_id': self.task_id,
            'kind': self.kind,
            'input': f'{INPUT_DIR}/{self.input_name}',
            'output_dir': OUTPUT_DIR,
            'expected_page_count': self.expected_page_count,
            # The renderer checks the file it was given against these before it
            # starts: a task directory is not proof of what is inside it.
            'input_sha256': self.copy.sha256,
            'input_size_bytes': self.input_path.stat().st_size if self.input_path.exists() else None,
            'timeout_seconds': self.settings.timeout_seconds,
            'dpi': self.settings.dpi,
            'want_page_images': self.settings.want_page_images,
            'source_slide_numbers': list(self.source_slide_numbers),
            'object_paths': list(self.object_paths),
        }


@dataclass
class RenderOutput:
    kind: str
    path: Path
    sha256: str
    size_bytes: int
    page_no: int | None = None

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'path': str(self.path),
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'page_no': self.page_no,
        }


@dataclass
class RenderOutcome:
    task_id: str
    status: str
    outputs: list[RenderOutput] = field(default_factory=list)
    page_count: int | None = None
    seconds: float | None = None
    renderer_version: str = ''
    soffice_version: str = ''
    issues: list[ParseIssue] = field(default_factory=list)
    reason: str | None = None

    @property
    def pdf(self) -> RenderOutput | None:
        return next((output for output in self.outputs if output.kind == 'preview_pdf'), None)

    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'status': self.status,
            'page_count': self.page_count,
            'seconds': self.seconds,
            'renderer_version': self.renderer_version,
            'soffice_version': self.soffice_version,
            'outputs': [output.to_dict() for output in self.outputs],
            'issues': [issue.to_dict() for issue in self.issues],
            'reason': self.reason,
        }


# --------------------------------------------------------------------------
# Building a task
# --------------------------------------------------------------------------

def _new_task_dir(task_root: Path, task_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task_id):
        raise RenderValidationError('invalid task_id')
    task_dir = Path(task_root).resolve() / task_id
    if task_dir.exists():
        raise RenderValidationError(f'task directory already exists: {task_dir}')
    (task_dir / INPUT_DIR).mkdir(parents=True)
    (task_dir / OUTPUT_DIR).mkdir(parents=True)
    return task_dir


def prepare_deck_task(document: ParsedDocument, source_path: Path, task_root: Path, *,
                      settings: RenderSettings | None = None, task_id: str | None = None) -> RenderTask:
    """A task that renders the visible slides of one deck.

    The input is a copy with the hidden slides and every note removed, so the
    renderer does not render those pages or notes. Unreferenced package assets
    may remain in this private task copy; the copy itself must never be served.
    """
    settings = settings or RenderSettings.from_env()
    if _sha256_of(Path(source_path)) != document.source_sha256:
        raise RenderValidationError('source hash does not match parsed document')
    task_id = task_id or f'deck-{uuid.uuid4().hex[:16]}'
    task_dir = _new_task_dir(task_root, task_id)
    input_name = f'{Path(source_path).stem}.odp'
    copy = write_render_copy(Path(source_path), task_dir / INPUT_DIR / input_name,
                             hidden_slides=document.hidden_slide_numbers)
    if _sha256_of(Path(source_path)) != document.source_sha256:
        raise RenderValidationError('source changed while preparing render copy')
    if copy.slide_order != document.visible_slide_numbers:
        raise RenderValidationError('render copy slide order differs from parsed document')
    task = RenderTask(
        task_id=task_id, kind='deck_preview', task_dir=task_dir, input_name=input_name,
        expected_page_count=len(document.pages), copy=copy,
        source_slide_numbers=copy.slide_order, settings=settings,
    )
    (task_dir / TASK_FILE).write_text(json.dumps(task.to_dict(), indent=2) + '\n', encoding='utf-8')
    return task


def prepare_object_task(source_path: Path, task_root: Path, *, source_slide_no: int,
                        object_paths: Sequence[str] | str,
                        settings: RenderSettings | None = None, task_id: str | None = None) -> RenderTask:
    """A task that renders the named figure objects on a page of their own.

    Every path given is kept in the copy. A drawing made of several shapes is
    rendered whole; a block whose preview_scope is 'page' does not belong here
    at all and needs prepare_deck_task instead.
    """
    settings = settings or RenderSettings.from_env()
    paths = (object_paths,) if isinstance(object_paths, str) else tuple(object_paths)
    task_id = task_id or f'object-{uuid.uuid4().hex[:16]}'
    task_dir = _new_task_dir(task_root, task_id)
    input_name = f'{Path(source_path).stem}-object.odp'
    copy = write_object_preview_copy(Path(source_path), task_dir / INPUT_DIR / input_name,
                                     source_slide_no=source_slide_no, object_paths=paths)
    task = RenderTask(
        task_id=task_id, kind='object_preview', task_dir=task_dir, input_name=input_name,
        expected_page_count=1, copy=copy, source_slide_numbers=(source_slide_no,),
        object_paths=paths, settings=settings,
    )
    (task_dir / TASK_FILE).write_text(json.dumps(task.to_dict(), indent=2) + '\n', encoding='utf-8')
    return task


def object_task_for_block(block, source_path: Path, task_root: Path, *, source_slide_no: int,
                          **kwargs) -> RenderTask:
    """Build an object task from a block that asks for one.

    A block whose preview covers the page is refused here rather than turned
    into a picture of one of its members.
    """
    if block.preview_scope != 'object' or not block.preview_object_id:
        raise RenderValidationError(
            f'block {block.block_id} needs a preview of the whole page, not of one object')
    if (block.source_ref.file_sha256 != _sha256_of(Path(source_path))
            or block.source_ref.source_slide_no != source_slide_no):
        raise RenderValidationError('object preview source does not match block')
    return prepare_object_task(source_path, task_root, source_slide_no=source_slide_no,
                               object_paths=(block.preview_object_id,), **kwargs)


# --------------------------------------------------------------------------
# Running a task
# --------------------------------------------------------------------------

def run_subprocess_renderer(task: RenderTask) -> None:
    """Start the configured renderer command on this task directory.

    The command comes from configuration. It is started without a shell, with a
    stripped environment, and with the task directory as its only argument
    substitution. Deciding how the component is isolated (a container with no
    network, a service account) belongs to that configuration, not here.
    """
    settings = task.settings
    if not settings.command:
        raise RendererUnavailable(
            'no renderer is configured: set SLIDE_RENDERER_COMMAND to the isolated renderer entry point'
        )
    command = [part.format(task_dir=str(task.task_dir), task_id=task.task_id) for part in settings.command]
    env = {key: os.environ[key] for key in RENDERER_ENV_ALLOWLIST if key in os.environ}
    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=env, start_new_session=True)
        code = process.wait(timeout=settings.timeout_seconds)
        if code:
            raise RenderFailed(f'the renderer exited with status {code}')
    except FileNotFoundError as exc:
        raise RendererUnavailable(f'the renderer command is not available: {exc}') from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderFailed(f'the renderer passed its {settings.timeout_seconds}s limit') from exc
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)


def render(task: RenderTask, *, runner=run_subprocess_renderer, page_count_reader=None) -> RenderOutcome:
    """Run one task and return only what passed validation."""
    runner(task)
    return collect_result(task, page_count_reader=page_count_reader)


# --------------------------------------------------------------------------
# Validating what came back
# --------------------------------------------------------------------------

def _checked_output_path(task: RenderTask, relative: str) -> Path:
    """Resolve one declared output inside the task's output directory."""
    if not isinstance(relative, str) or not relative:
        raise RenderValidationError('an output has no path')
    if relative.startswith('/') or (len(relative) > 1 and relative[1] == ':'):
        raise RenderValidationError(f'output path is absolute: {relative!r}')
    if '..' in Path(relative.replace('\\', '/')).parts:
        raise RenderValidationError(f'output path leaves the task directory: {relative!r}')
    parts = Path(relative.replace('\\', '/')).parts
    if not parts or parts[0] != OUTPUT_DIR:
        raise RenderValidationError(f'output path is not inside {OUTPUT_DIR}/: {relative!r}')
    if task.output_dir.is_symlink():
        raise RenderValidationError('output directory is a symlink')
    out_root = task.output_dir.resolve()
    walk = task.task_dir / relative
    # Every step from the output directory down must be a real entry: a symlink
    # anywhere on the way could point at a file we never asked for.
    step = task.output_dir
    for part in parts[1:]:
        step = step / part
        if step.is_symlink():
            raise RenderValidationError(f'output path goes through a symlink: {relative!r}')
    candidate = walk.resolve()
    if not str(candidate).startswith(str(out_root) + os.sep):
        raise RenderValidationError(f'output path is outside the task output directory: {relative!r}')
    if not candidate.is_file():
        raise RenderValidationError(f'declared output does not exist: {relative!r}')
    return candidate


def _sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pdf_page_count(path: Path) -> int:
    """Count the pages ourselves rather than trust the renderer's number."""
    import pymupdf

    with pymupdf.open(path) as document:
        return document.page_count


def pdf_page_texts(path: Path) -> list[str]:
    """Text of each preview page, used only to check the page mapping."""
    import pymupdf

    with pymupdf.open(path) as document:
        return [page.get_text() for page in document]


def collect_result(task: RenderTask, *, page_count_reader=None) -> RenderOutcome:
    """Read result.json and accept only what we could verify."""
    result_path = task.task_dir / RESULT_FILE
    if result_path.is_symlink() or (result_path.exists() and result_path.stat().st_size > 1024 * 1024):
        raise RenderValidationError('unsafe or oversized result file')
    if not result_path.is_file():
        raise RenderFailed(f'the renderer wrote no {RESULT_FILE} for task {task.task_id}')
    try:
        payload = json.loads(result_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderValidationError(f'{RESULT_FILE} could not be read: {exc}') from exc
    if not isinstance(payload, dict):
        raise RenderValidationError(f'{RESULT_FILE} is not an object')
    if payload.get('protocol') != RENDERER_PROTOCOL:
        raise RenderValidationError(
            f'renderer protocol {payload.get("protocol")!r}, this worker speaks {RENDERER_PROTOCOL}')
    if payload.get('task_id') != task.task_id:
        raise RenderValidationError(
            f'result belongs to task {payload.get("task_id")!r}, not to {task.task_id!r}')
    status = payload.get('status')
    if status != 'ok':
        raise RenderFailed(f'the renderer reported {status!r}: {payload.get("reason") or "no reason given"}')

    outputs: list[RenderOutput] = []
    declarations = payload.get('outputs') or []
    if not isinstance(declarations, list) or len(declarations) > 500:
        raise RenderValidationError('invalid output list or file count')
    seen = set()
    total = 0
    for declared in declarations:
        if not isinstance(declared, dict):
            raise RenderValidationError('an output entry is not an object')
        kind = declared.get('kind')
        if kind not in OUTPUT_KINDS:
            raise RenderValidationError(f'unknown output kind {kind!r}')
        path = _checked_output_path(task, declared.get('path'))
        if path in seen:
            raise RenderValidationError('duplicate output path')
        seen.add(path)
        size = path.stat().st_size
        total += size
        if total > 512 * 1024 * 1024:
            raise RenderValidationError('output byte budget exceeded')
        if declared.get('size_bytes') != size:
            raise RenderValidationError('output does not match its declared size')
        if kind != 'preview_pdf':
            expected_kind = 'object_png' if task.kind == 'object_preview' else 'page_png'
            number = declared.get('page_no')
            if kind != expected_kind or type(number) is not int or number < 1:
                raise RenderValidationError('invalid image kind or page number')
        elif declared.get('page_no') is not None:
            raise RenderValidationError('PDF must not declare a page number')
        if size == 0:
            raise RenderValidationError(f'output {declared.get("path")!r} is empty')
        with open(path, 'rb') as handle:
            head = handle.read(len(MAGIC[kind]))
        if head != MAGIC[kind]:
            raise RenderValidationError(
                f'output {declared.get("path")!r} does not look like a {kind}')
        actual = _sha256_of(path)
        if declared.get('sha256') != actual:
            raise RenderValidationError(f'output {declared.get("path")!r} does not match its declared hash')
        outputs.append(RenderOutput(kind=kind, path=path, sha256=actual, size_bytes=size,
                                    page_no=declared.get('page_no')))

    if not outputs:
        raise RenderFailed(f'task {task.task_id} produced no output')

    outcome = RenderOutcome(
        task_id=task.task_id, status='ok', outputs=outputs,
        seconds=payload.get('seconds'), renderer_version=payload.get('renderer_version') or '',
        soffice_version=payload.get('soffice_version') or '',
    )

    if sum(output.kind == 'preview_pdf' for output in outcome.outputs) > 1:
        raise RenderValidationError('multiple preview PDFs')
    pdf = outcome.pdf
    if pdf is None:
        raise RenderFailed(f'task {task.task_id} produced no preview PDF')

    counter = page_count_reader or pdf_page_count
    outcome.page_count = counter(pdf.path)
    if outcome.page_count != task.expected_page_count:
        # A page count that does not match the visible slides cannot be used as
        # a mapping, and it is not silently accepted either.
        outcome.status = 'page_count_mismatch'
        outcome.issues.append(ParseIssue(
            code='preview_mapping_failed', scope='document',
            message=(f'the preview has {outcome.page_count} pages for '
                     f'{task.expected_page_count} visible slides'),
        ))

    images = [output for output in outputs if output.kind in ('page_png', 'object_png')]
    if task.settings.want_page_images:
        # Asked for one picture per page: a partial set is a failed render, not
        # a set of pictures with holes in it.
        wanted = outcome.page_count or task.expected_page_count
        numbers = sorted(output.page_no for output in images if output.page_no is not None)
        if numbers != list(range(1, wanted + 1)):
            raise RenderFailed(
                f'task {task.task_id} asked for {wanted} page images and got {len(images)} '
                f'with page numbers {numbers}')
    elif images:
        raise RenderValidationError(
            f'task {task.task_id} did not ask for page images but {len(images)} came back')
    return outcome


# --------------------------------------------------------------------------
# Page mapping
# --------------------------------------------------------------------------

def _anchor(text: str) -> str:
    """Normalise for comparison only. Nothing normalised here is ever stored.

    The pairs below are the characters a renderer writes differently from the
    XML: a non-breaking space, typographic quotes, a ligature and long dashes.
    """
    for source, replacement in ((' ', ' '), ('’', "'"), ('‘', "'"),
                                ('œ', 'oe'), ('–', '-'), ('—', '-')):
        text = text.replace(source, replacement)
    return re.sub(r'\s+', ' ', text).strip().lower()


def _page_anchors(page) -> list[str]:
    """Anchor candidates from the native text of one page."""
    lines: list[str] = []
    for block in page.blocks:
        for line in (block.content or '').splitlines():
            anchor = _anchor(line)
            if len(anchor) >= ANCHOR_MIN_CHARS:
                lines.append(anchor)
    return sorted(lines, key=len, reverse=True)


@dataclass
class PageMappingEntry:
    source_slide_no: int
    page_id: str
    preview_page_no: int | None
    check: str
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            'source_slide_no': self.source_slide_no,
            'page_id': self.page_id,
            'preview_page_no': self.preview_page_no,
            'check': self.check,
            'reason': self.reason,
        }


@dataclass
class PreviewMapping:
    entries: list[PageMappingEntry] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    verified_pages: int = 0
    needs_review: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'entries': [entry.to_dict() for entry in self.entries],
            'verified_pages': self.verified_pages,
            'needs_review': list(self.needs_review),
            'issues': [issue.to_dict() for issue in self.issues],
        }


def verify_preview_mapping(document: ParsedDocument, page_texts: list[str]) -> PreviewMapping:
    """Check which preview page each visible slide actually landed on.

    The candidate mapping is the order of the visible slides, which is what the
    sanitised copy holds. That candidate is only a claim: each page is confirmed
    against a text anchor read from the source. Equal page counts alone leave
    every page in needs_review, which is the point of checking at all.
    """
    mapping = PreviewMapping()
    normalised = [_anchor(text) for text in page_texts]
    for position, page in enumerate(document.pages, start=1):
        target = position if position <= len(normalised) else None
        entry = PageMappingEntry(source_slide_no=page.source_slide_no, page_id=page.page_id,
                                 preview_page_no=target, check='pending')
        if target is None:
            entry.check = 'no_preview_page'
            entry.preview_page_no = None
            entry.reason = 'the preview has fewer pages than the deck has visible slides'
            mapping.issues.append(ParseIssue(
                code='preview_mapping_failed', scope='page', page_id=page.page_id,
                message=entry.reason,
            ))
            mapping.needs_review.append(page.source_slide_no)
            mapping.entries.append(entry)
            continue

        anchors = _page_anchors(page)
        if not anchors:
            entry.check = 'needs_visual_review'
            entry.reason = ('this slide has no text anchor long enough to check; '
                            'the page count alone does not prove the mapping')
            mapping.needs_review.append(page.source_slide_no)
            mapping.issues.append(ParseIssue(
                code='preview_mapping_failed', scope='page', page_id=page.page_id,
                message=entry.reason,
            ))
            mapping.entries.append(entry)
            continue

        anchor = anchors[0][:ANCHOR_COMPARE_CHARS]
        matches = [number for number, text in enumerate(normalised, start=1) if anchor in text]
        if matches == [target]:
            entry.check = 'anchor_unique_match'
            mapping.verified_pages += 1
        elif target in matches:
            entry.check = 'anchor_matches_but_not_unique'
            entry.reason = f'the anchor is also on pages {[number for number in matches if number != target]}'
            mapping.needs_review.append(page.source_slide_no)
        else:
            entry.check = 'anchor_not_found'
            entry.reason = (f'the anchor of slide {page.source_slide_no} is not on preview page {target}'
                            + (f'; it is on pages {matches}' if matches else ''))
            mapping.needs_review.append(page.source_slide_no)
            mapping.issues.append(ParseIssue(
                code='preview_mapping_failed', scope='page', page_id=page.page_id, message=entry.reason,
            ))
        mapping.entries.append(entry)
    return mapping


def apply_preview_mapping(document: ParsedDocument, mapping: PreviewMapping) -> ParsedDocument:
    """Write the checked mapping onto the pages, with its status kept visible."""
    by_page = {entry.page_id: entry for entry in mapping.entries}
    for page in document.pages:
        entry = by_page.get(page.page_id)
        if entry is None:
            continue
        page.preview_mapping_check = entry.check
        # A page whose mapping is not proven keeps no preview page number, so no
        # later step can quote a page we did not verify.
        page.preview_page_no = entry.preview_page_no if entry.check == 'anchor_unique_match' else None
        if entry.check != 'anchor_unique_match' and page.state == 'usable':
            page.state = 'partial'
    for issue in mapping.issues:
        if issue.scope == 'page' and issue.page_id:
            page = next((candidate for candidate in document.pages if candidate.page_id == issue.page_id), None)
            if page is not None and not any(
                existing.code == issue.code and existing.message == issue.message for existing in page.issues
            ):
                page.issues.append(issue)
        else:
            document.issues.append(issue)
    return document.validate()


def attach_preview_assets(document: ParsedDocument, outcome: RenderOutcome, *,
                          identity_version: str | None = None) -> list[ParsedAsset]:
    """Register the verified preview files as assets of the document.

    A page image is only attached to a page whose mapping was proven, so an
    unverified render cannot become the picture a student is shown.
    """
    if outcome.status != 'ok':
        raise RenderValidationError('cannot attach unsuccessful render')
    identity = identity_version or document.identity_version
    created: list[ParsedAsset] = []
    if sum(output.kind == 'preview_pdf' for output in outcome.outputs) > 1:
        raise RenderValidationError('multiple preview PDFs')
    pdf = outcome.pdf
    if pdf is not None:
        asset = ParsedAsset(
            asset_id=stable_id(identity, document.source_sha256, 'preview_pdf', pdf.sha256),
            kind='preview_pdf', mime='application/pdf', sha256=pdf.sha256,
            size_bytes=pdf.size_bytes, source_entry=str(pdf.path), access_class='student_visible',
        )
        if document.asset_by_id(asset.asset_id) is None:
            document.assets.append(asset)
            created.append(asset)
    by_preview_page = {
        page.preview_page_no: page for page in document.pages if page.preview_page_no is not None
    }
    for output in outcome.outputs:
        if output.kind != 'page_png' or output.page_no is None:
            continue
        page = by_preview_page.get(output.page_no)
        if page is None:
            continue
        asset = ParsedAsset(
            asset_id=stable_id(identity, document.source_sha256, 'page_preview', page.page_id, output.sha256),
            kind='page_preview', mime='image/png', sha256=output.sha256, size_bytes=output.size_bytes,
            source_entry=str(output.path), page_id=page.page_id, access_class='student_visible',
        )
        if document.asset_by_id(asset.asset_id) is None:
            document.assets.append(asset)
            created.append(asset)
        page.preview_asset_id = asset.asset_id
    document.validate()
    return created
