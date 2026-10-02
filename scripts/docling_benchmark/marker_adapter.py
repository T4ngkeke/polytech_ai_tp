"""Marker 2.0 adapter for the offline comparison. No application database access.

Variants:
  marker-text          fast mode, --disable_ocr: PDF text layer + small layout model, no VLM
  marker-balanced      balanced mode: surya VLM (local server on :18002) for layout and OCR
  marker-balanced-llm  balanced mode + --use_llm through the local Ornith server (:18001)
Marker has no review queue: its Markdown is the searchable text, as for the legacy C-api run.
Images are not extracted; with --use_llm Marker replaces them with an LLM description.
"""
import os
import time

import pymupdf

from .core import PageResult, Segment, add_review

VARIANTS = ('marker-text', 'marker-balanced', 'marker-balanced-llm')
LLM_SETTINGS = {'temperature': 0, 'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}}


def page_result(markdown, digest, page_no, variant, *, native, page_rect):
    result = PageResult(digest, page_no, variant)
    if markdown.strip():
        result.segments.append(Segment(markdown, native, {'file_sha256': digest, 'page_no': page_no, 'bbox': list(page_rect)}))
    else:
        add_review(result, {'file_sha256': digest, 'page_no': page_no}, 'No searchable content extracted')
    return result


class FixedParams:
    """Client wrapper: every structured-output call gets the fixed Ornith generation settings."""

    def __init__(self, client):
        self._client = client
        outer = self

        class Completions:
            def parse(self, **kwargs):
                return outer._client.chat.completions.parse(**{**kwargs, **LLM_SETTINGS})

        class Chat:
            completions = Completions()

        self.chat = Chat()


class MarkerAdapter:
    def __init__(self, variant, llm_url, surya_url, model):
        if variant not in VARIANTS:
            raise ValueError(f'Unknown Marker variant: {variant}')
        # surya reads these at import time: attach to the running server, never spawn docker.
        os.environ['SURYA_INFERENCE_URL'] = surya_url
        os.environ['SURYA_INFERENCE_AUTOSTART'] = 'false'
        from marker.config.parser import ConfigParser
        from marker.models import create_model_dict
        from marker.services.openai import OpenAIService
        self.variant, self.ConfigParser = variant, ConfigParser
        self.options = {'output_format': 'markdown', 'disable_image_extraction': True,
                        'mode': 'fast' if variant == 'marker-text' else 'balanced'}
        if variant == 'marker-text':
            self.options['disable_ocr'] = True
        if variant == 'marker-balanced-llm':
            self.options.update(use_llm=True, llm_service='marker.services.openai.OpenAIService',
                                openai_base_url=llm_url, openai_model=model, openai_api_key='local')
            original = OpenAIService.get_client
            OpenAIService.get_client = lambda service: FixedParams(original(service))
        started = time.monotonic()
        self.models = create_model_dict()
        self.initialization_seconds = time.monotonic() - started
        self.options_record = dict(self.options, llm_settings=LLM_SETTINGS if variant == 'marker-balanced-llm' else None,
                                   surya_model='datalab-to/surya-ocr-2')

    async def read(self, path, page_no, digest):
        start = time.monotonic()
        parser = self.ConfigParser(dict(self.options, page_range=str(page_no - 1)))
        converter = parser.get_converter_cls()(config=parser.generate_config_dict(), artifact_dict=self.models,
                                               processor_list=parser.get_processors(), renderer=parser.get_renderer(),
                                               llm_service=parser.get_llm_service())
        rendered = converter(str(path))
        with pymupdf.open(path) as pdf:
            page = pdf[page_no - 1]
            result = page_result(rendered.markdown, digest, page_no, self.variant, native=page.get_text(), page_rect=page.rect)
        result.metadata['marker_metadata'] = rendered.metadata
        result.seconds = time.monotonic() - start
        return result, {'markdown': rendered.markdown, 'metadata': rendered.metadata}
