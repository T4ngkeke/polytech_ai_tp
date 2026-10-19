"""Bounded MinerU hybrid page extraction; the only module importing MinerU.

The process running this function should be isolated from the API worker. A page
is parsed with the same options as the frozen September PDF experiment. The
returned content_list is data, never a database or publication instruction.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


FROZEN_MINERU_VERSION = '3.4.5'


class MinerUUnavailable(RuntimeError):
    pass


def extract_page(path: Path, page_no: int, output_dir: Path, *, server_url: str, config_path: str) -> list[dict]:
    if not Path(config_path).is_absolute() or not Path(config_path).is_file():
        raise MinerUUnavailable('mineru_config_unavailable')
    if not server_url:
        raise MinerUUnavailable('mineru_server_unavailable')
    os.environ['MINERU_TOOLS_CONFIG_JSON'] = config_path
    os.environ['MINERU_MODEL_SOURCE'] = 'local'
    try:
        from mineru.cli.common import do_parse
    except ImportError as exc:
        raise MinerUUnavailable('mineru_not_installed') from exc
    output_dir.mkdir(parents=True, exist_ok=False)
    do_parse(str(output_dir), ['page'], [path.read_bytes()], ['ch'],
             backend='hybrid-http-client', parse_method='auto', formula_enable=True,
             table_enable=True, server_url=server_url.removesuffix('/v1'), effort='high',
             image_analysis=True, f_draw_layout_bbox=False, f_draw_span_bbox=False,
             f_dump_md=True, f_dump_middle_json=False, f_dump_model_output=False,
             f_dump_orig_pdf=False, f_dump_content_list=True,
             start_page_id=page_no - 1, end_page_id=page_no - 1)
    matches = list(output_dir.glob('page/*/page_content_list.json'))
    if len(matches) != 1:
        raise RuntimeError('mineru_content_list_missing')
    value = json.loads(matches[0].read_text())
    if not isinstance(value, list):
        raise ValueError('mineru_content_list_invalid')
    return value


def subprocess_extractor(*, python_executable: str, server_url: str, config_path: str,
                         timeout_seconds: int = 240):
    """Use a separate interpreter so MinerU does not load into the main worker."""
    if not Path(python_executable).is_file():
        raise MinerUUnavailable('mineru_python_unavailable')
    if not Path(config_path).is_absolute() or not Path(config_path).is_file():
        raise MinerUUnavailable('mineru_config_unavailable')
    def run(path: Path, page_no: int, output_dir: Path) -> list[dict]:
        output_dir.mkdir(parents=True, exist_ok=False)
        command=[python_executable,'-m','backend.worker.pdf_mineru_cli','--pdf',str(path),
                 '--page',str(page_no),'--out',str(output_dir),'--server-url',server_url,
                 '--config',config_path]
        try:
            child_env=dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            completed=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     timeout=timeout_seconds,check=False,env=child_env)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('mineru_page_timeout') from exc
        if completed.returncode:
            # Logs may contain the source document or a secret endpoint. Keep the
            # worker error stable and inspect the isolated process separately.
            raise RuntimeError('mineru_page_failed')
        value=json.loads((output_dir/'result.json').read_text())
        if not isinstance(value,list): raise ValueError('mineru_content_list_invalid')
        return value
    return run
