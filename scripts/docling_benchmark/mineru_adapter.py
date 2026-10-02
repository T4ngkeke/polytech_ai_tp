"""MinerU 3.4 adapter for the offline comparison. No application database access.

Variants:
  mineru-pipeline  pipeline backend: local layout/OCR/formula/table models, PDF text layer when present, no VLM
  mineru-hybrid    hybrid-http-client, effort high: PDF text layer + MinerU2.5 VLM (local server on :18003),
                   image/chart analysis on
MinerU has no review queue: its Markdown (image file references removed) is the searchable text.
Models are read only from the local folders in MINERU_TOOLS_CONFIG_JSON (MINERU_MODEL_SOURCE=local).
"""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pymupdf

from .marker_adapter import page_result
from .markdown_tools import strip_images

VARIANTS = ('mineru-pipeline', 'mineru-hybrid')


class MinerUAdapter:
    def __init__(self, variant, server_url):
        if variant not in VARIANTS:
            raise ValueError(f'Unknown MinerU variant: {variant}')
        if not os.path.isabs(os.environ.get('MINERU_TOOLS_CONFIG_JSON', '')):
            raise ValueError('Set MINERU_TOOLS_CONFIG_JSON to the benchmark config (never ~/mineru.json)')
        os.environ['MINERU_MODEL_SOURCE'] = 'local'
        from mineru.cli.common import do_parse
        self.variant, self.do_parse = variant, do_parse
        self.options = {'backend': 'pipeline' if variant == 'mineru-pipeline' else 'hybrid-http-client',
                        'parse_method': 'auto', 'formula_enable': True, 'table_enable': True, 'lang': 'ch'}
        if variant == 'mineru-hybrid':
            # The client adds /v1/chat/completions itself.
            self.options.update(server_url=server_url.removesuffix('/v1'), effort='high', image_analysis=True)
        # Models load on the first page (MinerU keeps them in process-wide singletons).
        self.initialization_seconds = 0.0
        self.options_record = dict(self.options, server_url=None, vlm_model='opendatalab/MinerU2.5-Pro-2605-1.2B'
                                   if variant == 'mineru-hybrid' else None)

    async def read(self, path, page_no, digest):
        # MinerU's clients may run their own event loop, so the parse runs outside ours.
        return await asyncio.to_thread(self._read, path, page_no, digest)

    def _read(self, path, page_no, digest):
        start = time.monotonic()
        o = self.options
        with tempfile.TemporaryDirectory(prefix='mineru-page-') as out:
            self.do_parse(out, ['page'], [Path(path).read_bytes()], [o['lang']], backend=o['backend'],
                          parse_method=o['parse_method'], formula_enable=o['formula_enable'],
                          table_enable=o['table_enable'], server_url=o.get('server_url'),
                          f_draw_layout_bbox=False, f_draw_span_bbox=False, f_dump_md=True,
                          f_dump_middle_json=False, f_dump_model_output=False, f_dump_orig_pdf=False,
                          f_dump_content_list=True, start_page_id=page_no - 1, end_page_id=page_no - 1,
                          effort=o.get('effort', 'medium'), image_analysis=o.get('image_analysis', True))
            md_files = sorted(Path(out).glob('page/*/page.md'))
            if len(md_files) != 1:
                raise RuntimeError(f'Expected one Markdown file, found {len(md_files)}')
            markdown = md_files[0].read_text()
            content = sorted(Path(out).glob('page/*/page_content_list.json'))
            content_list = json.loads(content[0].read_text()) if content else None
        with pymupdf.open(path) as pdf:
            page = pdf[page_no - 1]
            result = page_result(strip_images(markdown), digest, page_no, self.variant,
                                 native=page.get_text(), page_rect=page.rect)
        result.seconds = time.monotonic() - start
        return result, {'markdown': markdown, 'content_list': content_list}
