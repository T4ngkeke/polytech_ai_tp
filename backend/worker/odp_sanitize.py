"""Copies of a presentation that are safe to hand to the renderer.

M2-A production module. The source file is opened read-only through the bounded
package reader and is never written to: every change here goes into a new file.

What a copy is not allowed to carry:

* speaker notes and author annotations, anywhere in the file and in the master
  pages, whether or not the deck has a hidden slide;
* hidden slides, so they cannot appear in a preview or in a page count that
  later gets used as a mapping;
* anything that would make the renderer reach outside the task directory: a
  link to an address or to a local file, a script, an event listener, an applet
  or a plugin. A container with no network does not cover ``file://``, and it
  does not cover a macro either.

The same bounds apply here as in the reader. A package with a member we refused
to read is refused for copying too: a preview made from it would look complete
while a part of the deck was missing.

The copy is written, then opened again and checked. A copy that still holds any
of the above is an error, not a warning.
"""
from __future__ import annotations

import zipfile
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from xml.etree import ElementTree as ET

from backend.worker.odf_package import (
    NS,
    OdfLimits,
    OdfPackage,
    OdfPackageError,
    Q,
    external_scheme,
    open_package,
    prefixed_name,
    sha256_file,
)
from backend.worker.odp_reader import NON_CONTENT_TAGS, _collect_drawing_page_styles, _resolve_visibility

for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

CONTENT_ENTRY = 'content.xml'
STYLES_ENTRY = 'styles.xml'
MIMETYPE_ENTRY = 'mimetype'

# Never rendered and never shown, wherever they sit, including in the master
# pages: speaker notes and author annotations.
STRIPPED_TAGS = ('presentation:notes', 'office:annotation')
# Code and anything that can act on its own. None of it is needed to draw a
# slide, and all of it is a way out of the task directory.
ACTIVE_TAGS = (
    'office:scripts', 'office:script', 'office:event-listeners', 'presentation:event-listener',
    'script:event-listener', 'office:forms', 'draw:control', 'draw:applet', 'draw:plugin',
    'draw:floating-frame', 'presentation:sound', 'anim:audio',
)
# An element that exists only to pull something in. With an address we refuse
# to follow, the element itself goes.
REFERENCE_TAGS = (
    'draw:image', 'draw:object', 'draw:object-ole', 'style:background-image', 'draw:fill-image',
)
# The nodes the reader does not count when it builds an object path. The two
# modules have to agree, or the object copy keeps the wrong shape.
SKIPPED_WHEN_COUNTING = frozenset({'presentation:notes'}) | NON_CONTENT_TAGS

# A drawing page with nothing on it but the object we keep: no master artwork,
# no header, footer, date or page number. Without it the master decoration
# fills the picture and the object cannot be told apart from the background.
BLANK_PAGE_STYLE = 'm2a-object-preview-page'
BLANK_STYLE_XML = (
    f'<style:style xmlns:style="{NS["style"]}" xmlns:draw="{NS["draw"]}" '
    f'xmlns:presentation="{NS["presentation"]}" style:name="{BLANK_PAGE_STYLE}" '
    'style:family="drawing-page">'
    '<style:drawing-page-properties draw:fill="solid" draw:fill-color="#ffffff" '
    'presentation:background-visible="false" presentation:background-objects-visible="false" '
    'presentation:display-header="false" presentation:display-footer="false" '
    'presentation:display-page-number="false" presentation:display-date-time="false"/>'
    '</style:style>'
)


class SanitizeError(RuntimeError):
    """The copy could not be produced, or it did not pass its own check."""


@dataclass
class SanitizedCopy:
    """One copy plus what was taken out of it."""

    path: Path
    sha256: str
    removed_hidden_slides: tuple[int, ...] = ()
    removed_notes_nodes: int = 0
    removed_annotation_nodes: int = 0
    removed_active_nodes: int = 0
    removed_external_references: int = 0
    # Source slide number in the order the copy holds them. The renderer's page
    # n corresponds to slide_order[n - 1]; this is a claim to verify, not a
    # proof, and verify_preview_mapping still has to check the content.
    slide_order: tuple[int, ...] = ()
    kept_object_paths: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def kept_object_path(self) -> str | None:
        return self.kept_object_paths[0] if self.kept_object_paths else None

    def to_dict(self) -> dict:
        return {
            'path': str(self.path),
            'sha256': self.sha256,
            'removed_hidden_slides': list(self.removed_hidden_slides),
            'removed_notes_nodes': self.removed_notes_nodes,
            'removed_annotation_nodes': self.removed_annotation_nodes,
            'removed_active_nodes': self.removed_active_nodes,
            'removed_external_references': self.removed_external_references,
            'slide_order': list(self.slide_order),
            'kept_object_paths': list(self.kept_object_paths),
            'notes': list(self.notes),
        }


@dataclass
class _Removals:
    notes: int = 0
    annotations: int = 0
    active: int = 0
    external: int = 0

    def add(self, other: '_Removals') -> None:
        self.notes += other.notes
        self.annotations += other.annotations
        self.active += other.active
        self.external += other.external


def _clean_tree(root: ET.Element) -> _Removals:
    """Take notes, annotations, code and outside references out of one part.

    The walk is over the whole tree, not over the children of a slide: a note
    can sit inside a shape, and a master page carries its own. Everything here
    is removed whatever the rest of the file looks like.
    """
    removals = _Removals()

    def walk(parent: ET.Element) -> None:
        for child in list(parent):
            tag = prefixed_name(child.tag)
            if tag == 'presentation:notes':
                parent.remove(child)
                removals.notes += 1
                continue
            if tag == 'office:annotation':
                parent.remove(child)
                removals.annotations += 1
                continue
            if tag in ACTIVE_TAGS:
                parent.remove(child)
                removals.active += 1
                continue
            href = child.attrib.get(Q['xlink'] + 'href')
            if href and external_scheme(href):
                removals.external += 1
                if tag in REFERENCE_TAGS:
                    parent.remove(child)
                    continue
                # A link on a piece of text: the text stays, the address goes.
                del child.attrib[Q['xlink'] + 'href']
            walk(child)

    walk(root)
    href = root.attrib.get(Q['xlink'] + 'href')
    if href and external_scheme(href):
        del root.attrib[Q['xlink'] + 'href']
        removals.external += 1
    return removals


def _presentation_of(content: ET.Element, name: str) -> ET.Element:
    presentation = next(iter(content.iter(Q['office'] + 'presentation')), None)
    if presentation is None:
        raise SanitizeError(f'{name}: the file has no office:presentation element')
    return presentation


def _write_copy(package: OdfPackage, target: Path, parts: dict[str, ET.Element]) -> None:
    """Write the copy: the rewritten parts, and every other member as it was.

    Only members the bounded reader accepted are copied. There is no path back
    to the raw archive here, so a member that was over a limit cannot slip into
    the copy.
    """
    payloads = {
        name: ET.tostring(root, encoding='UTF-8', xml_declaration=True)
        for name, root in parts.items()
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as out:
        # ODF wants the mimetype first and stored uncompressed.
        if package.has(MIMETYPE_ENTRY):
            out.writestr(zipfile.ZipInfo(MIMETYPE_ENTRY), package.read(MIMETYPE_ENTRY),
                         compress_type=zipfile.ZIP_STORED)
        for name in package.entries:
            if name == MIMETYPE_ENTRY:
                continue
            out.writestr(name, payloads.get(name) or package.read(name))


def _verify_copy(target: Path, *, expected_slides: int, limits: OdfLimits) -> None:
    """Open the copy again and check it against its own promises."""
    try:
        with open_package(target, limits) as package:
            package.require_complete(target.name)
            parts = [name for name in package.entries if name.endswith('.xml') and package.read(name).strip()]
            for name in parts:
                root = package.xml(name)
                for tag in STRIPPED_TAGS + ACTIVE_TAGS:
                    prefix, _, local = tag.partition(':')
                    if prefix not in Q:
                        continue
                    found = list(root.iter(Q[prefix] + local))
                    if found:
                        raise SanitizeError(f'{target.name}: {name} still holds {len(found)} {tag} nodes')
                for element in root.iter():
                    href = element.attrib.get(Q['xlink'] + 'href')
                    if href and external_scheme(href):
                        raise SanitizeError(
                            f'{target.name}: {name} still points outside the package: {href!r}')
                if name == CONTENT_ENTRY:
                    slides = len(list(root.iter(Q['draw'] + 'page')))
                    if slides != expected_slides:
                        raise SanitizeError(
                            f'{target.name}: the copy holds {slides} slides, expected {expected_slides}')
    except OdfPackageError as exc:
        raise SanitizeError(f'{target.name}: the copy is not a package we accept: {exc}') from exc


def _open_source(source_path: Path, limits: OdfLimits) -> OdfPackage:
    try:
        package = open_package(source_path, limits)
    except OdfPackageError as exc:
        raise SanitizeError(f'{Path(source_path).name}: {exc}') from exc
    try:
        package.require_complete(Path(source_path).name)
    except OdfPackageError as exc:
        package.close()
        raise SanitizeError(str(exc)) from exc
    if not package.has(CONTENT_ENTRY):
        package.close()
        raise SanitizeError(f'{Path(source_path).name}: the package has no {CONTENT_ENTRY}')
    return package


def _parts_to_clean(package: OdfPackage) -> dict[str, ET.Element]:
    return {name: package.xml(name) for name in package.entries if name.endswith('.xml') and package.read(name).strip()}


def write_render_copy(source_path: Path, target: Path, *, hidden_slides: Sequence[int],
                      limits: OdfLimits | None = None) -> SanitizedCopy:
    """A copy with the hidden slides, all notes and all active content removed.

    ``hidden_slides`` are 1-based source slide numbers, normally taken from the
    reader so the copy and the IR agree on what is visible.
    """
    source_path = Path(source_path)
    if source_path.resolve() == Path(target).resolve() or Path(target).exists():
        raise SanitizeError('target must be a new file distinct from source')
    limits = limits or OdfLimits()
    hidden = {int(number) for number in hidden_slides}
    removals = _Removals()
    with _open_source(source_path, limits) as package:
        try:
            parts = _parts_to_clean(package)
            content = parts[CONTENT_ENTRY]
            presentation = _presentation_of(content, source_path.name)
            kept: list[int] = []
            for index, page in enumerate(list(presentation.findall(Q['draw'] + 'page')), start=1):
                if index in hidden:
                    presentation.remove(page)
                    continue
                kept.append(index)
            if not kept:
                raise SanitizeError(f'{source_path.name}: every slide is hidden, there is nothing to render')
            for root in parts.values():
                removals.add(_clean_tree(root))
            _write_copy(package, target, parts)
        except OdfPackageError as exc:
            raise SanitizeError(f'{source_path.name}: {exc}') from exc

    _verify_copy(target, expected_slides=len(kept), limits=limits)
    return SanitizedCopy(
        path=target,
        sha256=sha256_file(target),
        removed_hidden_slides=tuple(sorted(hidden)),
        removed_notes_nodes=removals.notes,
        removed_annotation_nodes=removals.annotations,
        removed_active_nodes=removals.active,
        removed_external_references=removals.external,
        slide_order=tuple(kept),
        notes=['notes are removed from every copy, with or without hidden slides',
               'scripts, event listeners and outside references are removed as well'],
    )


def _object_steps(object_path: str) -> list[tuple[str, int]]:
    """The steps of a reader object path below the slide.

    ``…/draw:page[3]/draw:g[1]/draw:frame[2]`` gives
    ``[('draw:g', 1), ('draw:frame', 2)]``. A nested object has to be found by
    walking, not by matching its last step against the slide's own children:
    that would keep the wrong shape.
    """
    parts = object_path.split('/')
    try:
        start = next(index for index, part in enumerate(parts) if part.startswith('draw:page[')) + 1
    except StopIteration as exc:
        raise SanitizeError(f'{object_path!r} does not name an object on a slide') from exc
    steps: list[tuple[str, int]] = []
    for part in parts[start:]:
        name, _, index = part.partition('[')
        try:
            steps.append((name, int(index.rstrip(']') or 1)))
        except ValueError as exc:
            raise SanitizeError(f'{object_path!r} is not an object path this module understands') from exc
    if not steps:
        raise SanitizeError(f'{object_path!r} names a slide, not an object on it')
    return steps


def _keep_paths(page: ET.Element, wanted: list[list[tuple[str, int]]]) -> int:
    """Keep every branch in ``wanted`` and drop the rest of the slide.

    Several paths are kept together on purpose. A drawing made of three shapes
    is not covered by rendering one of them, and a group is followed step by
    step rather than matched on its last step, which would find a different
    shape with the same tag at the top level.
    """
    removed = 0

    def descend(node: ET.Element, branches: list[list[tuple[str, int]]]) -> None:
        nonlocal removed
        heads = {branch[0] for branch in branches}
        counts: dict[str, int] = {}
        children: list[tuple[tuple[str, int], ET.Element]] = []
        for child in list(node):
            tag = prefixed_name(child.tag)
            if tag in SKIPPED_WHEN_COUNTING:
                continue
            counts[tag] = counts.get(tag, 0) + 1
            children.append(((tag, counts[tag]), child))
        found = {step for step, _ in children}
        missing = heads - found
        if missing:
            step_text = ', '.join(f'{tag}[{index}]' for tag, index in sorted(missing))
            raise SanitizeError(f'the slide has no {step_text} to keep')
        for step, child in children:
            if step not in heads:
                node.remove(child)
                removed += 1
                continue
            deeper = [branch[1:] for branch in branches if branch[0] == step and len(branch) > 1]
            if deeper and not any(len(branch) == 1 for branch in branches if branch[0] == step):
                descend(child, deeper)

    descend(page, wanted)
    return removed


def write_object_preview_copy(source_path: Path, target: Path, *, source_slide_no: int,
                              object_paths: Sequence[str] | str,
                              limits: OdfLimits | None = None) -> SanitizedCopy:
    """A copy holding one slide with the named objects on it, for a figure preview.

    Rendering the objects on their own avoids the two failures seen in M0: a
    crop taken from the page can cut a drawing that spills past its frame, and
    it can swallow the body text beside it. Every path given is kept, so a
    drawing made of several shapes is rendered whole; when no set of objects
    covers the figure, the caller asks for the page instead of calling one
    member the figure. The page preview stays available either way, because a
    single object out of its page can lose the context a reader needs.
    """
    source_path = Path(source_path)
    if source_path.resolve() == Path(target).resolve() or Path(target).exists():
        raise SanitizeError('target must be a new file distinct from source')
    limits = limits or OdfLimits()
    paths = [object_paths] if isinstance(object_paths, str) else list(object_paths)
    if not paths:
        raise SanitizeError('an object preview needs at least one object path')
    for path in paths:
        page_match = re.search(r'/draw:page\[(\d+)\]/', path)
        if not page_match or int(page_match.group(1)) != source_slide_no:
            raise SanitizeError('object path belongs to another slide')
    branches = [_object_steps(path) for path in paths]
    removals = _Removals()
    with _open_source(source_path, limits) as package:
        try:
            parts = _parts_to_clean(package)
            content = parts[CONTENT_ENTRY]
            presentation = _presentation_of(content, source_path.name)
            pages = presentation.findall(Q['draw'] + 'page')
            if source_slide_no > len(pages) or source_slide_no < 1:
                raise SanitizeError(f'{source_path.name}: there is no slide {source_slide_no}')
            keep = pages[source_slide_no - 1]
            styles = _collect_drawing_page_styles(content)
            for name, entry in _collect_drawing_page_styles(parts.get(STYLES_ENTRY, ET.Element('empty'))).items():
                styles.setdefault(name, entry)
            if _resolve_visibility(keep.get(Q['draw'] + 'style-name'), styles)[0] == 'hidden':
                raise SanitizeError('hidden slide cannot have an object preview')
            for page in pages:
                if page is not keep:
                    presentation.remove(page)
            removed = _keep_paths(keep, branches)
            for root in parts.values():
                removals.add(_clean_tree(root))
            if not len(keep):
                raise SanitizeError(
                    f'{source_path.name}: nothing is left of slide {source_slide_no} after cleaning')

            automatic = next(iter(content.iter(Q['office'] + 'automatic-styles')), None)
            if automatic is None:
                automatic = ET.Element(Q['office'] + 'automatic-styles')
                content.insert(0, automatic)
            automatic.append(ET.fromstring(BLANK_STYLE_XML))
            keep.set(Q['draw'] + 'style-name', BLANK_PAGE_STYLE)
            _write_copy(package, target, parts)
        except OdfPackageError as exc:
            raise SanitizeError(f'{source_path.name}: {exc}') from exc

    _verify_copy(target, expected_slides=1, limits=limits)
    return SanitizedCopy(
        path=target,
        sha256=sha256_file(target),
        removed_notes_nodes=removals.notes,
        removed_annotation_nodes=removals.annotations,
        removed_active_nodes=removals.active,
        removed_external_references=removals.external,
        slide_order=(source_slide_no,),
        kept_object_paths=tuple(paths),
        notes=[f'{removed} sibling objects were left out of this copy',
               'an object on its own can lose the context of its page; the page preview is still needed'],
    )
