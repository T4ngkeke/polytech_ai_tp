"""Immutable development suite and run fingerprints."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / 'documentation/worker-docling-tuned-2026-09-14'
OUTPUT = ROOT / 'documentation/worker-docling-benchmark'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_path(name):
    """Resolve legacy manifests after the corpus moved into pdf_files."""
    direct = ROOT / 'cours' / name
    return direct if direct.exists() else ROOT / 'cours/pdf_files' / name


def load_suite(folder=HISTORY):
    """Load a page set: <folder>/manifest.json (files, cases) and <folder>/checks.json."""
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text())
    checks = json.loads((folder / 'checks.json').read_text())
    for name, digest in manifest['files'].items():
        if sha(source_path(name)) != digest:
            raise ValueError(f'Input changed: {name}')
    for stem, entries in checks.items():
        for entry in entries:
            entry['check_id'] = f"{stem}/{entry['id']}"
    return {'dataset': 'development-28-v1' if folder == HISTORY else folder.name, 'files': manifest['files'],
            'cases': manifest['cases'], 'checks': checks,
            'checks_sha256': sha(folder / 'checks.json')}


def freeze(folder, config):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'config.json'
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError('Run configuration changed; use a new run directory')
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2))


def code_fingerprint():
    paths = list(Path(__file__).parent.glob('*.py'))
    paths += list((ROOT / 'backend/worker').glob('*.py'))
    # The legacy adapter loads exactly these function definitions, without running scripts.
    paths += [HISTORY / 'native_code.py', HISTORY / 'run_docling_tuned.py']
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}
