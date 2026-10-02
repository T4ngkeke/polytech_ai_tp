"""PaddleOCR 3.7 adapter for the offline comparison. No application database access.

Variants:
  paddle-structure  PP-StructureV3 (layout, OCR, table and formula models), default settings, on CPU
  paddle-vl         PaddleOCR-VL-1.6: PP-DocLayoutV3 layout on CPU, PaddleOCR-VL-1.6 VLM on a local vLLM
                    server (:18004)
PaddlePaddle has no GPU build for this ARM machine, so the Paddle models run on CPU.
Both read the rendered page (no PDF text layer). PaddleOCR has no review queue: its Markdown (image file
references removed) is the searchable text. Models are cached under PADDLE_PDX_CACHE_HOME.
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

import pymupdf

from .marker_adapter import page_result
from .markdown_tools import strip_images

VARIANTS = ('paddle-structure', 'paddle-vl')


class PaddleAdapter:
    def __init__(self, variant, server_url):
        if variant not in VARIANTS:
            raise ValueError(f'Unknown PaddleOCR variant: {variant}')
        if not os.path.isabs(os.environ.get('PADDLE_PDX_CACHE_HOME', '')):
            raise ValueError('Set PADDLE_PDX_CACHE_HOME to the benchmark model cache (never ~/.paddlex)')
        from paddleocr import PPStructureV3, PaddleOCRVL
        self.variant = variant
        if variant == 'paddle-structure':
            self.options = {'pipeline': 'PP-StructureV3', 'device': 'cpu'}
        else:
            self.options = {'pipeline': 'PaddleOCR-VL', 'pipeline_version': 'v1.6', 'device': 'cpu',
                            'vl_rec_backend': 'vllm-server', 'vl_rec_server_url': server_url}
        started = time.monotonic()
        if variant == 'paddle-structure':
            self.pipeline = PPStructureV3(device='cpu')
        else:
            self.pipeline = PaddleOCRVL(pipeline_version='v1.6', device='cpu', vl_rec_backend='vllm-server',
                                        vl_rec_server_url=server_url)
        self.initialization_seconds = time.monotonic() - started
        self.options_record = dict(self.options, vl_rec_server_url=None, vlm_model='PaddlePaddle/PaddleOCR-VL-1.6'
                                   if variant == 'paddle-vl' else None)

    async def read(self, path, page_no, digest):
        # PaddleX's GenAI client runs its own event loop, so the prediction runs outside ours.
        return await asyncio.to_thread(self._read, path, page_no, digest)

    def _read(self, path, page_no, digest):
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='paddle-page-') as out, pymupdf.open(path) as pdf:
            single = pymupdf.open()
            single.insert_pdf(pdf, from_page=page_no - 1, to_page=page_no - 1)
            target = Path(out) / 'page.pdf'
            single.save(target)
            single.close()
            results = list(self.pipeline.predict(str(target)))
            if len(results) != 1:
                raise RuntimeError(f'Expected one page result, found {len(results)}')
            markdown = results[0].markdown['markdown_texts']
            page = pdf[page_no - 1]
            result = page_result(strip_images(markdown), digest, page_no, self.variant,
                                 native=page.get_text(), page_rect=page.rect)
            raw = {'markdown': markdown, 'result': results[0].json}
        result.seconds = time.monotonic() - start
        return result, raw
