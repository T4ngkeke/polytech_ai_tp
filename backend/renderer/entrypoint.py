"""Entry point of the isolated slide renderer.

It reads one task directory and writes its result back into the same directory.
That directory is the only thing it is given: no database, no model credential,
no document library, no docker socket, and, when it is started as intended, no
network at all.

Protocol
--------

Input  ``<task>/task.json``  written by backend/worker/slide_renderer.py
Input  ``<task>/in/<file>``  the presentation copy to render
Output ``<task>/out/…``      the produced PDF and PNG files
Output ``<task>/result.json``what was produced, with hashes

What this entry point checks for itself, rather than trusting the caller:

* the task directory holds no symlink on the way to the input or the output,
  and both stay inside it;
* the input is the file the task says it is, by size and by hash, and it opens
  as an OpenDocument presentation package, not just as something starting with
  "PK";
* the input and the produced output stay inside their byte budgets;
* the output directory is empty before the work starts, so a file left over
  from an earlier run can never be reported as this run's result;
* the conversion ended by itself, with status zero, inside the deadline. On a
  timeout the whole process group is killed, not just the child we started;
* a timeout or a DPI asked for by the task cannot go past the caps of this
  deployment.

Every attempt gets a fresh LibreOffice profile, the total run is bounded, and
``result.json`` is written in one move so a reader never sees half of it.

Only the Python standard library is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import signal
import subprocess
import sys
import time
import tempfile
import zipfile
from pathlib import Path

try:
    from backend.worker.odf_package import open_package, OdfPackageError, external_scheme, Q
except ModuleNotFoundError:
    from odf_package import open_package, OdfPackageError, external_scheme, Q


RENDERER_VERSION = 'slide-renderer-m2a-2'
PROTOCOL = 1
MAX_ATTEMPTS = 2
PRESENTATION_MIME = 'application/vnd.oasis.opendocument.presentation'

# A fresh profile per attempt, with macros off and nothing loaded from outside.
# The container has no network either: this is one layer, not the only one.
PROFILE_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Save/Document">
  <prop oor:name="CreateBackup" oor:op="fuse"><value>false</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Recovery/AutoSave">
  <prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop>
 </item>
</oor:items>
"""


class TaskError(RuntimeError):
    """The task is not one we accept, or it could not be completed."""


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
        if value <= 0:
            raise TaskError(f'{name} must be positive')
        return value
    except ValueError:
        return default


def caps() -> dict:
    """The bounds of this deployment. A task cannot ask to go past them."""
    return {
        'cpu_seconds': _int_env('RENDERER_MAX_CPU_SECONDS', 240),
        'address_space_bytes': _int_env('RENDERER_MAX_ADDRESS_SPACE_BYTES', 3 * 1024 ** 3),
        'file_size_bytes': _int_env('RENDERER_MAX_FILE_SIZE_BYTES', 512 * 1024 * 1024),
        'open_files': _int_env('RENDERER_MAX_OPEN_FILES', 1024),
        'timeout_seconds': _int_env('RENDERER_MAX_TIMEOUT_SECONDS', 300),
        'total_seconds': _int_env('RENDERER_MAX_TOTAL_SECONDS', 600),
        'dpi': _int_env('RENDERER_MAX_DPI', 200),
        'input_bytes': _int_env('RENDERER_MAX_INPUT_BYTES', 200 * 1024 * 1024),
        'output_bytes': _int_env('RENDERER_MAX_OUTPUT_BYTES', 512 * 1024 * 1024),
        'output_files': _int_env('RENDERER_MAX_OUTPUT_FILES', 500),
    }


def _apply_limits(limits: dict):
    def preexec() -> None:
        # Its own process group, so a timeout can take the whole tree with it.
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (limits['cpu_seconds'], limits['cpu_seconds']))
        resource.setrlimit(resource.RLIMIT_AS, (limits['address_space_bytes'], limits['address_space_bytes']))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits['file_size_bytes'], limits['file_size_bytes']))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits['open_files'], limits['open_files']))

    return preexec


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def contained_path(task_dir: Path, relative: str, *, kind: str) -> Path:
    """Resolve one path inside the task directory, refusing any way out of it.

    Each step is checked, so a symlinked parent directory cannot move the
    target somewhere else after the name itself looked fine.
    """
    if not isinstance(relative, str) or not relative:
        raise TaskError(f'the task has no {kind} path')
    if relative.startswith('/') or (len(relative) > 1 and relative[1] == ':'):
        raise TaskError(f'the {kind} path is absolute: {relative!r}')
    parts = Path(relative.replace('\\', '/')).parts
    if '..' in parts or '.' in parts:
        raise TaskError(f'the {kind} path leaves the task directory: {relative!r}')
    step = task_dir
    for part in parts:
        step = step / part
        if step.is_symlink():
            raise TaskError(f'the {kind} path goes through a symlink: {relative!r}')
    resolved = step.resolve()
    root = task_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise TaskError(f'the {kind} path is outside the task directory: {relative!r}')
    return step


def _check_package(source: Path) -> None:
    """Open the input as an ODF package before handing it to the office suite.

    A file that merely starts with "PK" is not a presentation. This does not
    replace the reader's checks; it is the renderer refusing to start work on
    something the task did not promise.
    """
    try:
        with open_package(source) as package:
            package.require_complete(source.name)
            for name in package.entries:
                if not name.endswith('.xml') or not package.read(name).strip():
                    continue
                root = package.xml(name)
                for element in root.iter():
                    local = element.tag.rsplit('}', 1)[-1]
                    if local in ('scripts', 'script', 'event-listeners', 'event-listener',
                                 'applet', 'plugin') or element.tag in (Q['presentation'] + 'notes', Q['office'] + 'annotation'):
                        raise TaskError('input contains notes or active content')
                    if external_scheme(element.get(Q['xlink'] + 'href', '')):
                        raise TaskError('input contains an external resource')
    except OdfPackageError as exc:
        raise TaskError(f'the input is not a readable package: {exc}') from exc


def load_task(task_dir: Path) -> dict:
    """Read task.json and check everything it claims about its own input."""
    task_file = task_dir / 'task.json'
    if task_file.is_symlink() or not task_file.is_file():
        raise TaskError(f'no task.json in {task_dir}')
    try:
        task = json.loads(task_file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskError(f'task.json could not be read: {exc}') from exc
    if not isinstance(task, dict):
        raise TaskError('task.json is not an object')
    if task.get('protocol') != PROTOCOL:
        raise TaskError(f'task protocol {task.get("protocol")!r}, this renderer speaks {PROTOCOL}')
    if not task.get('task_id'):
        raise TaskError('the task has no task_id')

    relative = str(task.get('input') or '')
    if not relative.startswith('in/'):
        raise TaskError(f'the task input is not inside the task directory: {relative!r}')
    source = contained_path(task_dir, relative, kind='input')
    if source.is_symlink() or not source.is_file():
        raise TaskError(f'the task input is missing or is a symlink: {relative!r}')

    bounds = caps()
    size = source.stat().st_size
    if size > bounds['input_bytes']:
        raise TaskError(f'the input is {size} bytes, over the {bounds["input_bytes"]} byte cap')
    declared_size = task.get('input_size_bytes')
    if declared_size is not None and int(declared_size) != size:
        raise TaskError(f'the input is {size} bytes, the task says {declared_size}')
    declared_hash = task.get('input_sha256')
    if declared_hash:
        actual = sha256_file(source)
        if actual != declared_hash:
            raise TaskError('the input does not match the hash in the task')
    _check_package(source)
    if declared_size is None or not declared_hash:
        raise TaskError('input size and hash are required')
    return task


def _run(command: list[str], *, timeout: int, limits: dict, log: Path) -> dict:
    """One bounded attempt. On a timeout the whole process group is killed."""
    started = time.monotonic()
    record = {'command': command[0], 'timed_out': False, 'return_code': None}
    process = None
    # Regular files avoid unbounded PIPE buffers; child RLIMIT_FSIZE bounds logs.
    with tempfile.TemporaryFile() as capture:
        try:
            process = subprocess.Popen(command, stdout=capture, stderr=capture,
                                       preexec_fn=_apply_limits(limits))
            while process.poll() is None:
                _collect_outputs(log.parent / 'out', log.parent, limits)
                if time.monotonic() - started >= timeout:
                    record['timed_out'] = True
                    break
                time.sleep(0.05)
            record['return_code'] = process.poll()
        except OSError as exc:
            record['return_code'] = -1
            capture.write(str(exc).encode())
        finally:
            if process is not None:
                _kill_group(process)
        capture.seek(0, os.SEEK_END)
        capture.seek(max(0, capture.tell() - 4000))
        tail = capture.read().decode('utf-8', errors='replace')
    record['seconds'] = round(time.monotonic() - started, 2)
    with open(log, 'a', encoding='utf-8') as handle:
        handle.write(f"rc={record['return_code']} timeout={record['timed_out']}\n{tail}\n")
    return record


def _kill_group(process: subprocess.Popen) -> None:
    """Stop the child and everything it started."""
    # The group leader can exit before its descendants. Kill by the original
    # session/group id even when the leader has already been reaped.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def soffice_version() -> str:
    try:
        result = subprocess.run(['soffice', '--version'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    text = (result.stdout or result.stderr or '').strip()
    return text.splitlines()[0] if text else 'unknown'


def pdf_page_count(pdf: Path) -> int | None:
    try:
        result = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'^Pages:\s+(\d+)', result.stdout, re.M)
    return int(match.group(1)) if match else None


def _require_empty_output(out_dir: Path) -> None:
    """Refuse to start on top of an earlier run's files.

    Without this, a PDF left behind by a previous attempt could be reported as
    the result of this one.
    """
    if out_dir.is_symlink():
        raise TaskError('the output directory is a symlink')
    if not out_dir.is_dir():
        out_dir.mkdir(parents=True)
        return
    leftovers = sorted(entry.name for entry in out_dir.iterdir())
    if leftovers:
        raise TaskError(f'the output directory is not empty before the run: {leftovers[:5]}')


def _collect_outputs(out_dir: Path, task_dir: Path, bounds: dict) -> list[dict]:
    """List what was produced, inside the output budget."""
    outputs = []
    total = 0
    for path in sorted(out_dir.rglob('*')):
        if path.is_symlink():
            raise TaskError(f'the renderer produced a symlink: {path.name}')
        if path.is_dir():
            continue
        if not path.is_file():
            raise TaskError('renderer output is not a regular file')
        size = path.stat().st_size
        total += size
        if total > bounds['output_bytes']:
            raise TaskError(f'the output passes the {bounds["output_bytes"]} byte cap')
        outputs.append({'path': path, 'size_bytes': size})
        if len(outputs) > bounds['output_files']:
            raise TaskError(f'the output passes the {bounds["output_files"]} file cap')
    return outputs


def render_task(task_dir: Path) -> dict:
    """Do the work and return the result payload."""
    task = load_task(task_dir)
    bounds = caps()
    log = contained_path(task_dir, 'render.log', kind='log')
    out_dir = contained_path(task_dir, 'out', kind='output')
    _require_empty_output(out_dir)
    source = contained_path(task_dir, str(task['input']), kind='input')

    # A task may ask for less than the deployment allows, never for more.
    timeout = min(int(task.get('timeout_seconds') or 180), bounds['timeout_seconds'])
    dpi = min(int(task.get('dpi') or 110), bounds['dpi'])
    if timeout <= 0 or dpi <= 0:
        raise TaskError('timeout and DPI must be positive')
    clamped = {
        'timeout_seconds': int(task.get('timeout_seconds') or 180) != timeout,
        'dpi': int(task.get('dpi') or 110) != dpi,
    }
    deadline = time.time() + bounds['total_seconds']

    attempts = []
    produced = out_dir / (source.stem + '.pdf')
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = int(deadline - time.time())
        if remaining <= 0:
            raise TaskError(f'the task passed its total {bounds["total_seconds"]}s budget')
        # A fresh profile per attempt: a profile left in a bad state by the
        # attempt before is a reason the second one fails too.
        profile = Path(tempfile.mkdtemp(prefix=f'profile-{attempt}-', dir=task_dir))
        (profile / 'user').mkdir()
        (profile / 'user' / 'registrymodifications.xcu').write_text(PROFILE_XCU, encoding='utf-8')
        record = _run([
            'soffice', '--headless', '--norestore', '--nolockcheck', '--nodefault',
            '--nofirststartwizard', '--invisible',
            f'-env:UserInstallation=file://{profile}',
            '--convert-to', 'pdf:impress_pdf_Export',
            '--outdir', str(out_dir), str(source),
        ], timeout=min(timeout, remaining), limits=bounds, log=log)
        attempts.append(record)
        if record['timed_out']:
            # A deadline reached is a failure with a reason, not something to
            # try again until it works.
            raise TaskError(f'the conversion passed its {timeout}s limit')
        if record['return_code'] == 0 and produced.is_file():
            break
        if attempt == MAX_ATTEMPTS:
            raise TaskError(
                f'the conversion ended with status {record["return_code"]} and no usable PDF '
                f'after {attempt} attempts')
        # Nothing from the failed attempt is allowed to stay behind.
        for leftover in out_dir.iterdir():
            leftover.unlink()

    if not produced.is_file():
        raise TaskError('the conversion reported success but wrote no PDF')

    page_count = pdf_page_count(produced)
    if page_count is None or page_count < 1:
        raise TaskError('could not verify PDF page count')
    expected = task.get('expected_page_count')
    if expected is not None and page_count is not None and int(expected) != page_count:
        # The worker checks this as well. Saying it here means a wrong preview
        # never gets reported as a finished task.
        raise TaskError(f'the preview has {page_count} pages, the task expects {expected}')

    if task.get('want_page_images'):
        remaining = int(deadline - time.time())
        if remaining <= 0:
            raise TaskError(f'the task passed its total {bounds["total_seconds"]}s budget')
        record = _run(['pdftoppm', '-png', '-r', str(dpi), str(produced), str(out_dir / 'page')],
                      timeout=min(timeout, remaining), limits=bounds, log=log)
        attempts.append(record)
        if record['timed_out'] or record['return_code'] != 0:
            raise TaskError(f'the page images ended with status {record["return_code"]}')

    outputs = []
    images = []
    for entry in _collect_outputs(out_dir, task_dir, bounds):
        path = entry['path']
        relative = path.relative_to(task_dir).as_posix()
        if path == produced:
            outputs.append({'kind': 'preview_pdf', 'path': relative, 'sha256': sha256_file(path),
                            'size_bytes': entry['size_bytes'], 'page_no': None})
            continue
        match = re.search(r'page-0*(\d+)\.png$', path.name)
        if match is None:
            raise TaskError(f'the renderer produced a file nobody asked for: {relative}')
        images.append({
            'kind': 'object_png' if task.get('kind') == 'object_preview' else 'page_png',
            'path': relative, 'sha256': sha256_file(path), 'size_bytes': entry['size_bytes'],
            'page_no': int(match.group(1)),
        })

    if task.get('want_page_images'):
        wanted = page_count if page_count is not None else int(expected or 0)
        numbers = sorted(image['page_no'] for image in images)
        if wanted and numbers != list(range(1, wanted + 1)):
            raise TaskError(f'{len(images)} page images for {wanted} pages: {numbers}')
    elif images:
        raise TaskError('page images were produced but the task did not ask for them')
    outputs.extend(images)

    return {
        'protocol': PROTOCOL,
        'task_id': task['task_id'],
        'status': 'ok',
        'kind': task.get('kind'),
        'outputs': outputs,
        'page_count': page_count,
        'seconds': round(sum(attempt['seconds'] for attempt in attempts), 2),
        'attempts': attempts,
        'renderer_version': RENDERER_VERSION,
        'soffice_version': soffice_version(),
        'limits': bounds,
        'clamped': clamped,
        'dpi': dpi,
        'timeout_seconds': timeout,
        'reason': None,
    }


def _write_result(task_dir: Path, payload: dict) -> None:
    """Write result.json in one move, so no reader sees half of it."""
    target = task_dir / 'result.json'
    temporary = task_dir / 'result.json.part'
    if temporary.is_symlink():
        raise TaskError('result temporary path is a symlink')
    temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, target)


def main(argv: list[str]) -> int:
    task_dir = Path(argv[1] if len(argv) > 1 else '/task').resolve()
    try:
        payload = render_task(task_dir)
    except Exception as exc:  # noqa: BLE001 — every failure is reported, not hidden
        task_id = ''
        try:
            task_id = json.loads((task_dir / 'task.json').read_text(encoding='utf-8')).get('task_id', '')
        except Exception:  # noqa: BLE001
            pass
        payload = {
            'protocol': PROTOCOL,
            'task_id': task_id,
            'status': 'failed',
            'outputs': [],
            'renderer_version': RENDERER_VERSION,
            'reason': f'{type(exc).__name__}: {exc}',
        }
        _write_result(task_dir, payload)
        return 1
    _write_result(task_dir, payload)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
