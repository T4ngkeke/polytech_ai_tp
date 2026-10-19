"""M2-B: files of structured documents under the storage root.

Keys are made here by the server, never from an uploaded filename. A write goes
to ``<key>.part`` first and is then linked into place, so nobody sees half a
file and an existing object is never overwritten. The caller records the key in
document_staged_objects before writing, so an interrupted write is still found
and removed by the cleanup sweep.
"""
import hashlib
import os
import shutil
from pathlib import Path

from backend.app.services.document_versions import storage_key

PART_SUFFIX = '.part'
_CHUNK = 1024 * 1024


class StorageError(RuntimeError):
    """A storage path or object that must not be used. The message is a code."""


def source_key(document_id, source_id, fmt: str) -> str:
    return storage_key(f'{document_id}/sources/{source_id}.{fmt}')


def asset_key(document_id, lock_token, asset_id, extension: str) -> str:
    # The claim token is part of the key: a retry never collides with a file an
    # earlier, abandoned attempt may still have on disk.
    return storage_key(f'{document_id}/assets/{lock_token}/{asset_id}{extension}')


def resolve(root, key: str) -> Path:
    """The path of a key. Refuses anything that would leave the root."""
    storage_key(key)
    base = Path(root).resolve()
    path = base / key
    # The file may not exist yet, so check its folder. A symlinked folder that
    # points outside the root is refused like a '..' in the key.
    parent = path.parent
    while not parent.exists() and parent != base:
        parent = parent.parent
    if not parent.resolve().is_relative_to(base) or path == base:
        raise StorageError('unsafe_storage_key')
    if path.is_symlink():
        raise StorageError('unsafe_storage_key')
    return path


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(_CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def write_object(root, key: str, source: bytes | Path, *, sha256: str) -> int:
    """Write one new object and check its hash. Returns its size in bytes."""
    path = resolve(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = resolve(root, key)  # again: a folder may have appeared as a symlink
    if path.exists():
        raise StorageError('storage_key_exists')
    part = path.with_name(path.name + PART_SUFFIX)
    part.unlink(missing_ok=True)  # left by an earlier crash of this same key
    h = hashlib.sha256()
    try:
        with open(part, 'xb') as out:
            if isinstance(source, (bytes, bytearray)):
                h.update(source)
                out.write(source)
            else:
                with open(source, 'rb') as handle:
                    for block in iter(lambda: handle.read(_CHUNK), b''):
                        h.update(block)
                        out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if h.hexdigest() != sha256:
            raise StorageError('object_hash_mismatch')
        # link, not rename: link fails if the target exists, rename would replace it.
        os.link(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path.stat().st_size


def verified_path(root, key: str, sha256: str) -> Path:
    """The path of an existing object whose bytes still have the recorded hash."""
    path = resolve(root, key)
    if not path.is_file():
        raise StorageError('source_missing')
    if _digest(path) != sha256:
        raise StorageError('source_hash_mismatch')
    return path


def copy_verified(root, key: str, sha256: str, target: Path) -> Path:
    """Copy an object out of storage for a job, and check the copy's hash.

    The job reads its own copy, so the stored original is never opened by the
    parser or the renderer.
    """
    shutil.copyfile(verified_path(root, key, sha256), target)
    if _digest(target) != sha256:
        raise StorageError('source_hash_mismatch')
    return target


def remove_object(root, key: str) -> None:
    """Remove an object and any partial write of it. Missing files are fine."""
    path = resolve(root, key)
    path.unlink(missing_ok=True)
    path.with_name(path.name + PART_SUFFIX).unlink(missing_ok=True)
