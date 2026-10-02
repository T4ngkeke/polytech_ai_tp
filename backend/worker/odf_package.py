"""Read-only access to an OpenDocument package, with the limits applied up front.

M2-A production module. The M0 experiment (scripts/odp_m0) is never imported at
runtime; the checks that proved useful there are re-implemented here with the
refusals the execution plan asks for: no path escape, no entity declaration, no
unbounded expansion, and no silent drop of an entry we refused to read.

Nothing in this module writes to the source file.
"""
from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from xml.parsers import expat

# The OpenDocument namespaces we read. A tag outside this map keeps its raw name
# and is reported as unknown rather than dropped.
NS = {
    'script': 'urn:oasis:names:tc:opendocument:xmlns:script:1.0',
    'anim': 'urn:oasis:names:tc:opendocument:xmlns:animation:1.0',
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
    'presentation': 'urn:oasis:names:tc:opendocument:xmlns:presentation:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'svg': 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
    'xlink': 'http://www.w3.org/1999/xlink',
    'manifest': 'urn:oasis:names:tc:opendocument:xmlns:manifest:1.0',
    'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
    'xml': 'http://www.w3.org/XML/1998/namespace',
    'math': 'http://www.w3.org/1998/Math/MathML',
}
Q = {prefix: '{%s}' % uri for prefix, uri in NS.items()}

PRESENTATION_MIME = 'application/vnd.oasis.opendocument.presentation'
FORMULA_MIME = 'application/vnd.oasis.opendocument.formula'
SPREADSHEET_MIME_PREFIX = 'application/vnd.oasis.opendocument.spreadsheet'
GRAPHICS_MIME_PREFIX = 'application/vnd.oasis.opendocument.graphics'

EXTERNAL_SCHEMES = ('http://', 'https://', 'ftp://', 'file://', 'smb://', 'data:')


class OdfPackageError(RuntimeError):
    """The package cannot be read safely. The reason is always carried along."""


@dataclass(frozen=True)
class OdfLimits:
    """Resource limits for one package.

    A limit that is reached is recorded and reported. It is never turned into an
    empty result, and content is never cut down quietly to fit.
    """

    max_archive_bytes: int = 200 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    max_entry_bytes: int = 64 * 1024 * 1024
    max_entries: int = 20_000
    max_compression_ratio: int = 200
    ratio_check_min_bytes: int = 1024 * 1024

    def as_dict(self) -> dict:
        return {
            'max_archive_bytes': self.max_archive_bytes,
            'max_total_bytes': self.max_total_bytes,
            'max_entry_bytes': self.max_entry_bytes,
            'max_entries': self.max_entries,
            'max_compression_ratio': self.max_compression_ratio,
            'ratio_check_min_bytes': self.ratio_check_min_bytes,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def local_name(tag: str) -> str:
    return tag.split('}', 1)[1] if '}' in tag else tag


def prefixed_name(tag: str) -> str:
    """draw:frame style name, or the raw tag when the namespace is unknown to us."""
    if '}' not in tag:
        return tag
    uri, name = tag[1:].split('}', 1)
    for prefix, known in NS.items():
        if known == uri:
            return f'{prefix}:{name}'
    return f'{{{uri}}}{name}'


def _fix_name(name: str) -> str:
    """expat writes ``uri}local``; ElementTree expects ``{uri}local``."""
    return '{' + name if '}' in name else name


def parse_xml(data: bytes, *, where: str) -> ET.Element:
    """Parse one XML part, refusing any doctype or entity declaration.

    The declaration is refused by the parser itself, through expat's own
    handlers, rather than by looking at the start of the file. Scanning a fixed
    number of leading bytes is not a check: a long comment pushes the
    declaration past it, and an encoding such as UTF-16 hides the words
    altogether. Here the position and the encoding make no difference, and no
    entity is ever expanded, so a declaration cannot turn into an allocation
    bomb either.

    External entity references are refused as well, so nothing is fetched and no
    file outside the package is read.
    """
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator='}')
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = True

    def reject_doctype(name, *_args):
        raise OdfPackageError(f'{where}: a doctype declaration is not accepted ({name!r})')

    def reject_entity(name, *_args):
        raise OdfPackageError(f'{where}: an entity declaration is not accepted ({name!r})')

    def start(tag, attributes):
        builder.start(_fix_name(tag), {_fix_name(key): value for key, value in attributes.items()})

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.EntityDeclHandler = reject_entity
    parser.UnparsedEntityDeclHandler = reject_entity
    parser.ExternalEntityRefHandler = lambda *_args: 0
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda tag: builder.end(_fix_name(tag))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(data, True)
        return builder.close()
    except expat.ExpatError as exc:
        raise OdfPackageError(f'{where}: malformed XML: {exc}') from exc


def safe_entry_name(name: str) -> str:
    """Return the entry name, or raise if it is unsafe or ambiguous.

    An ambiguous name is refused rather than repaired. ``./content.xml`` and
    ``content.xml`` name the same part, and a package that carries both leaves
    the reader and the office suite free to pick different ones. The same goes
    for a doubled separator or a backslash: they are not normalised here, they
    are rejected, so the name we check is the name everything else will use.
    """
    if not name:
        raise OdfPackageError('empty ZIP entry name')
    if '\x00' in name:
        raise OdfPackageError(f'ZIP entry name contains a NUL byte: {name!r}')
    if '\\' in name:
        raise OdfPackageError(f'ZIP entry name uses a backslash separator: {name!r}')
    if name.startswith('/'):
        raise OdfPackageError(f'ZIP entry is an absolute path: {name!r}')
    if len(name) > 1 and name[1] == ':':
        raise OdfPackageError(f'ZIP entry carries a drive letter: {name!r}')
    parts = name.rstrip('/').split('/')
    if '..' in parts or '..' in PurePosixPath(name).parts:
        raise OdfPackageError(f'ZIP entry escapes the package root: {name!r}')
    if any(part in ('', '.') for part in parts):
        raise OdfPackageError(f'ZIP entry name is ambiguous: {name!r}')
    return name


def _refuse_special_entry(info: zipfile.ZipInfo) -> None:
    """Refuse a member that is not a plain file or a directory.

    A ZIP can carry the unix mode of its members. A symlink member would make
    an extracting tool point at something outside the package, so it is refused
    here rather than handled later.
    """
    if info.create_system != 3:  # not unix, so there is no mode to read
        return
    file_type = stat.S_IFMT(info.external_attr >> 16)
    # Many writers store permission bits only. A member that does say what it
    # is has to say regular file or directory.
    if file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR):
        raise OdfPackageError(
            f'ZIP entry {info.filename!r} is not a plain file '
            f'(mode {stat.filemode(info.external_attr >> 16)})')


def external_scheme(href: str) -> str | None:
    """The scheme of an href we will not fetch, or None when it stays in the package."""
    value = href.strip().replace('\\', '/')
    if value.startswith('/') or '..' in PurePosixPath(value).parts:
        return 'external-path'
    match = re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*):', value)
    return match.group(1).lower() if match else None


def package_relative_href(href: str) -> str | None:
    """Turn an href into a package entry name, or None when it is not one.

    An href that leaves the package, or that names something outside it, returns
    None so the caller can report it instead of reading a file from the host.
    """
    if not href or external_scheme(href):
        return None
    candidate = href.strip().replace('\\', '/')
    if candidate.startswith('/'):
        return None
    while candidate.startswith('./'):
        candidate = candidate[2:]
    candidate = candidate.rstrip('/')
    if not candidate:
        return None
    if '..' in PurePosixPath(candidate).parts:
        return None
    return candidate


@dataclass
class OdfPackage:
    """One opened package. Every entry listed here already passed the limits."""

    path: Path
    sha256: str
    size_bytes: int
    entries: dict[str, dict]
    manifest: dict[str, str] = field(default_factory=dict)
    limit_hits: list[dict] = field(default_factory=list)
    limits: OdfLimits = field(default_factory=OdfLimits)
    _zip: zipfile.ZipFile | None = None

    def has(self, name: str) -> bool:
        return name in self.entries

    def read(self, name: str) -> bytes:
        if self._zip is None:
            raise OdfPackageError('package is closed')
        if name not in self.entries:
            raise OdfPackageError(f'{self.path.name}: no entry {name!r} available to read')
        return self._zip.read(name)

    def xml(self, name: str) -> ET.Element:
        return parse_xml(self.read(name), where=f'{self.path.name}:{name}')

    def media_type(self, entry: str) -> str:
        return self.manifest.get(entry.rstrip('/'), '')

    def names_under(self, folder: str) -> list[str]:
        prefix = folder.rstrip('/') + '/'
        return sorted(name for name in self.entries if name.startswith(prefix))

    @property
    def skipped_entries(self) -> list[str]:
        """Members a limit kept us from reading. They are never copied anywhere."""
        return [hit['entry'] for hit in self.limit_hits]

    def require_complete(self, where: str) -> None:
        """Refuse to go on when part of the package was never read.

        A copy built from a package with a skipped member would be missing that
        member, and the preview made from it would look complete while it is
        not. The caller is told instead.
        """
        if self.limit_hits:
            names = ', '.join(sorted(self.skipped_entries)[:5])
            raise OdfPackageError(
                f'{where}: {len(self.limit_hits)} package members were over a limit and not read '
                f'({names}); this file cannot be copied or rendered as if it were complete')

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> 'OdfPackage':
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _read_manifest(archive: zipfile.ZipFile, entries: dict[str, dict], where: str) -> dict[str, str]:
    name = 'META-INF/manifest.xml'
    if name not in entries:
        return {}
    root = parse_xml(archive.read(name), where=f'{where}:{name}')
    out: dict[str, str] = {}
    for entry in root.iter(Q['manifest'] + 'file-entry'):
        full_path = entry.attrib.get(Q['manifest'] + 'full-path')
        media = entry.attrib.get(Q['manifest'] + 'media-type') or ''
        if full_path:
            out[full_path.rstrip('/')] = media
    return out


def open_package(path: Path, limits: OdfLimits | None = None, *, expect_mime: str | None = PRESENTATION_MIME) -> OdfPackage:
    """Open an ODF package for reading.

    Raises OdfPackageError when the archive itself is not acceptable: too large,
    a path that escapes the root, an expansion or ratio over the limit, or a
    declared type we did not ask for. A single oversized entry is skipped and
    recorded in limit_hits, so the caller can report the gap rather than believe
    the file had nothing there.
    """
    limits = limits or OdfLimits()
    path = Path(path)
    if not path.is_file():
        raise OdfPackageError(f'{path}: not a file')
    size = path.stat().st_size
    if size > limits.max_archive_bytes:
        raise OdfPackageError(f'{path.name}: archive is {size} bytes, over the {limits.max_archive_bytes} byte limit')
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise OdfPackageError(f'{path.name}: not a readable ZIP: {exc}') from exc

    entries: dict[str, dict] = {}
    limit_hits: list[dict] = []
    total = 0
    try:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise OdfPackageError(f'{path.name}: {len(infos)} entries, over the {limits.max_entries} entry limit')
        # Every member is checked, including the ones a limit later skips: a
        # duplicate or an alias is a property of the package, not of what we
        # chose to read.
        seen: set[str] = set()
        seen_lower: dict[str, str] = {}
        for info in infos:
            name = safe_entry_name(info.filename)
            _refuse_special_entry(info)
            if name.endswith('/'):
                continue
            if name in seen:
                raise OdfPackageError(f'{path.name}: entry {name!r} appears more than once')
            previous = seen_lower.get(name.lower())
            if previous is not None:
                raise OdfPackageError(
                    f'{path.name}: entries {previous!r} and {name!r} differ only by case')
            seen.add(name)
            seen_lower[name.lower()] = name
            ratio = info.file_size / max(info.compress_size, 1)
            if info.file_size >= limits.ratio_check_min_bytes and ratio > limits.max_compression_ratio:
                raise OdfPackageError(
                    f'{path.name}: entry {name!r} expands {ratio:.0f} times, over the '
                    f'{limits.max_compression_ratio} times limit'
                )
            if info.file_size > limits.max_entry_bytes:
                limit_hits.append({'entry': name, 'reason': 'entry_over_limit', 'size_bytes': info.file_size})
                continue
            total += info.file_size
            if total > limits.max_total_bytes:
                raise OdfPackageError(
                    f'{path.name}: expanded size passes {limits.max_total_bytes} bytes'
                )
            entries[name] = {
                'name': name,
                'size_bytes': info.file_size,
                'compressed_bytes': info.compress_size,
            }

        if expect_mime is not None:
            declared = archive.read('mimetype').decode('ascii', 'replace').strip() if 'mimetype' in entries else ''
            manifest_root_type = ''
            manifest = _read_manifest(archive, entries, path.name)
            manifest_root_type = manifest.get('/', '')
            if declared and declared != expect_mime:
                raise OdfPackageError(f'{path.name}: declared type {declared!r}, expected {expect_mime!r}')
            if not declared and manifest_root_type and manifest_root_type != expect_mime:
                raise OdfPackageError(
                    f'{path.name}: manifest type {manifest_root_type!r}, expected {expect_mime!r}'
                )
            if not declared and not manifest_root_type:
                raise OdfPackageError(f'{path.name}: the package does not declare its type')
        else:
            manifest = _read_manifest(archive, entries, path.name)
    except Exception:
        archive.close()
        raise

    return OdfPackage(
        path=path,
        sha256=sha256_file(path),
        size_bytes=size,
        entries=entries,
        manifest=manifest,
        limit_hits=limit_hits,
        limits=limits,
        _zip=archive,
    )
