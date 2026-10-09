"""One bounded, opt-in live visual observation for a research run.

This never edits extracted text. Only the caller may read an authorized asset;
the VLM receives image bytes and a narrow question, never a storage URL.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from openai import AsyncOpenAI

from backend.app.services.model_routing import Endpoint


@dataclass(frozen=True)
class VisualReply:
    observation: str
    uncertainties: tuple[str, ...]
    model: str
    input_tokens: int | None
    output_tokens: int | None


_SCHEMA = {'type': 'json_schema', 'json_schema': {
    'name': 'live_visual_observation', 'strict': True,
    'schema': {'type': 'object', 'additionalProperties': False,
               'required': ['observation', 'uncertainties'],
               'properties': {'observation': {'type': 'string'},
                              'uncertainties': {'type': 'array',
                                                'items': {'type': 'string'}}}}}}


class ResearchVision:
    def __init__(self, endpoint: Endpoint):
        if not endpoint.base_url or not endpoint.model:
            raise ValueError('vision_model_not_configured')
        self.endpoint = endpoint
        self.client = AsyncOpenAI(base_url=endpoint.base_url,
                                  api_key=endpoint.api_key or 'local', max_retries=0)

    async def __call__(self, image: bytes, mime: str, question: str) -> VisualReply:
        if mime not in ('image/png', 'image/jpeg') or len(image) > 3_000_000:
            raise ValueError('invalid_image')
        data = base64.b64encode(image).decode('ascii')
        response = await self.client.chat.completions.create(
            model=self.endpoint.model, max_tokens=700, temperature=0,
            timeout=18, response_format=_SCHEMA,
            extra_body={'chat_template_kwargs': {'enable_thinking': False}}, messages=[{
                'role': 'user', 'content': [
                    {'type': 'text', 'text': (
                        'Answer the narrow question about this course image. '
                        'Describe only what is visibly supported; report unreadable labels, '
                        'ambiguous arrows and uncertainty. No course-wide inferences. '
                        'Return JSON with observation and uncertainties. Question: ' + question)},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}}
                ]}])
        payload = json.loads(response.choices[0].message.content or '')
        observation = payload.get('observation')
        uncertainties = payload.get('uncertainties')
        if (not isinstance(observation, str) or not observation.strip() or
                len(observation) > 1200 or not isinstance(uncertainties, list) or
                len(uncertainties) > 8 or
                any(not isinstance(x, str) or len(x) > 250 for x in uncertainties)):
            raise ValueError('invalid_visual_response')
        usage = response.usage
        return VisualReply(observation.strip(), tuple(uncertainties),
                           response.model or self.endpoint.model,
                           usage.prompt_tokens if usage else None,
                           usage.completion_tokens if usage else None)
