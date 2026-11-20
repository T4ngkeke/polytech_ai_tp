"""chat_file_service.py — files a student uploads in the chat.

The Open WebUI way, not the course-file way: no worker, no MinerU, no index.

- An image (PNG, JPEG, GIF, WebP) is kept as it is; the model looks at it.
- A PDF keeps the text of its text layer, read at upload with PyMuPDF, page
  by page. A scanned PDF has no text layer: ``has_text`` is false, and the
  page asks the student for a screenshot instead.
- A text file (code, notes, data) keeps its text.

The type is read from the file's first bytes (images, PDF), not from its name;
a text file must have a known extension and no binary bytes. The bytes are
stored under DOCUMENTS_STORAGE_ROOT/chat_files/<user id>/.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path, PurePath

import pymupdf
from sqlalchemy import select

from ..agent.tools import readable_files
from ..models import ChatFile, Document, Session

MAX_BYTES = 10 * 1024 * 1024
TEXT_MAX_CHARS = 200_000

TEXT_EXTENSIONS = {
    '.txt', '.md', '.csv', '.tsv', '.json', '.xml', '.yaml', '.yml', '.ini', '.toml', '.log',
    '.py', '.ipynb', '.c', '.h', '.cpp', '.hpp', '.java', '.js', '.ts', '.html', '.css', '.sql',
    '.sh', '.r', '.m', '.tex', '.rs', '.go', '.php', '.rb', '.kt', '.swift', '.asm', '.vhd', '.v',
}


class ChatFileError(ValueError):
    """A file that cannot be kept; ``code`` is sent to the page."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _decode_text(data: bytes) -> str | None:
    if b'\x00' in data[:8192]:
        return None
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _pdf_text(data: bytes) -> str:
    """The text layer, page by page ("[page 2]" between pages)."""
    try:
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            parts = []
            for number, page in enumerate(pdf, start=1):
                text = page.get_text().strip()
                if text:
                    parts.append(f'[page {number}]\n{text}')
                if sum(len(p) for p in parts) > TEXT_MAX_CHARS:
                    break
    except Exception:
        raise ChatFileError('unreadable_pdf') from None
    return '\n\n'.join(parts)


def read_file(filename: str, data: bytes) -> tuple[str, str, str | None]:
    """(kind, mime, text) of an uploaded file; ChatFileError when it cannot be kept."""
    if not data:
        raise ChatFileError('empty_file')
    if len(data) > MAX_BYTES:
        raise ChatFileError('file_too_large')
    mime = _image_mime(data)
    if mime:
        return 'image', mime, None
    if data.startswith(b'%PDF'):
        return 'pdf', 'application/pdf', _pdf_text(data)
    if PurePath(filename).suffix.lower() in TEXT_EXTENSIONS:
        text = _decode_text(data)
        if text is not None:
            return 'text', 'text/plain', text
    raise ChatFileError('unsupported_type')


def clean_name(filename: str | None) -> str:
    name = PurePath((filename or '').replace('\\', '/')).name.strip() or 'file'
    return name[:255]


def stored_path(root: str, chat_file: ChatFile) -> Path:
    return Path(root) / chat_file.storage_key


async def save(db, *, root: str, user_id: uuid.UUID, lab_id: uuid.UUID | None,
               filename: str | None, data: bytes) -> ChatFile:
    """Check, read and store an upload; adds the row (the caller commits)."""
    name = clean_name(filename)
    kind, mime, text = await asyncio.to_thread(read_file, name, data)
    file_id = uuid.uuid4()
    suffix = PurePath(name).suffix.lower()[:10]
    key = f'chat_files/{user_id}/{file_id}{suffix}'
    target = Path(root) / key
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, data)
    text = (text or '').strip()
    row = ChatFile(id=file_id, user_id=user_id, lab_id=lab_id, filename=name, mime=mime,
                   size=len(data), kind=kind, storage_key=key,
                   text=text[:TEXT_MAX_CHARS] or None, text_chars=len(text), has_text=bool(text))
    db.add(row)
    await db.flush()
    return row


class AttachmentError(Exception):
    """An attachment the question may not use: HTTP ``status`` and ``code``."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


async def resolve_attachments(db, refs, *, user_id: uuid.UUID, session_id: uuid.UUID,
                              lab_id: uuid.UUID, class_id: uuid.UUID, exam_mode: bool,
                              allowed: dict[str, bool]) -> list[dict]:
    """Check what a question attaches; returns [{kind, id, name, ...}] to store.

    - file: an own, not hidden upload of this lab; needs the ``upload`` feature.
    - document: a course file this lab can read now (in exam mode: an allowed one).
    - session: an own, not deleted, earlier chat of this lab; not in exam mode.
    """
    out, seen = [], set()
    for ref in refs:
        if (ref.kind, ref.id) in seen:
            continue
        seen.add((ref.kind, ref.id))
        if ref.kind == 'file':
            if not allowed.get('upload'):
                raise AttachmentError(403, 'feature_off')
            row = await db.get(ChatFile, ref.id)
            if row is None or row.user_id != user_id or row.deleted_at is not None or row.lab_id != lab_id:
                raise AttachmentError(404, 'attachment_unavailable')
            out.append({'kind': 'file', 'id': str(row.id), 'name': row.filename,
                        'file_kind': row.kind, 'has_text': row.has_text})
        elif ref.kind == 'document':
            readable = readable_files(class_id, lab_id, exam_only=exam_mode).subquery()
            name = await db.scalar(select(Document.filename).where(
                Document.id == ref.id, Document.id.in_(select(readable.c[0]))))
            if name is None:
                raise AttachmentError(404, 'attachment_unavailable')
            out.append({'kind': 'document', 'id': str(ref.id), 'name': name})
        else:
            if exam_mode:
                raise AttachmentError(403, 'exam_mode')
            row = await db.get(Session, ref.id)
            if (row is None or row.user_id != user_id or row.is_deleted or row.id == session_id
                    or row.lab_id != lab_id):
                raise AttachmentError(404, 'attachment_unavailable')
            out.append({'kind': 'session', 'id': str(row.id), 'name': row.title})
    return out
