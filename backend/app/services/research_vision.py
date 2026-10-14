"""One bounded, opt-in live visual observation for a research run.

This never edits extracted text. Only the caller may read an authorized asset;
the VLM receives image bytes and a narrow question, never a storage URL.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from backend.app.agent.model import QWEN_NON_THINKING
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


# Long replies are shortened, not refused: a good observation with one long
# uncertainty is still evidence.
MAX_OBSERVATION = 2000
MAX_UNCERTAINTIES = 8
MAX_UNCERTAINTY = 300
SHORTENED = ' […]'


class VisionError(ValueError):
    """Why a visual observation failed. code: vision_timeout, vision_bad_output
    or vision_unreachable; detail is for the trace, not for the model."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - len(SHORTENED)].rstrip() + SHORTENED


class ResearchVision:
    def __init__(self, endpoint: Endpoint, *, timeout: float = 45.0, max_tokens: int = 1200):
        if not endpoint.base_url or not endpoint.model:
            raise ValueError('vision_model_not_configured')
        self.endpoint, self.timeout, self.max_tokens = endpoint, timeout, max_tokens
        self.client = AsyncOpenAI(base_url=endpoint.base_url,
                                  api_key=endpoint.api_key or 'local', max_retries=0)

    async def __call__(self, image: bytes, mime: str, question: str) -> VisualReply:
        if mime not in ('image/png', 'image/jpeg') or len(image) > 3_000_000:
            raise ValueError('invalid_image')
        data = base64.b64encode(image).decode('ascii')
        try:
            response = await self._create(mime, data, question)
        except APITimeoutError:
            raise VisionError('vision_timeout', f'no reply within {self.timeout:g} s') from None
        except APIStatusError as exc:
            raise VisionError('vision_unreachable', f'HTTP {exc.status_code}') from None
        except APIConnectionError:
            raise VisionError('vision_unreachable', 'connection failed') from None
        choice = response.choices[0]
        if choice.finish_reason == 'length':
            raise VisionError('vision_bad_output', f'reply cut at {self.max_tokens} tokens')
        try:
            payload = json.loads(choice.message.content or '')
        except ValueError:
            raise VisionError('vision_bad_output', 'reply is not JSON') from None
        observation = payload.get('observation') if isinstance(payload, dict) else None
        uncertainties = payload.get('uncertainties') if isinstance(payload, dict) else None
        if not isinstance(observation, str) or not observation.strip():
            raise VisionError('vision_bad_output', 'no observation')
        if not isinstance(uncertainties, list):
            uncertainties = []
        uncertainties = [_clip(x.strip(), MAX_UNCERTAINTY) for x in uncertainties
                         if isinstance(x, str) and x.strip()][:MAX_UNCERTAINTIES]
        usage = response.usage
        return VisualReply(_clip(observation.strip(), MAX_OBSERVATION), tuple(uncertainties),
                           response.model or self.endpoint.model,
                           usage.prompt_tokens if usage else None,
                           usage.completion_tokens if usage else None)

    async def _create(self, mime: str, data: str, question: str):
        return await self.client.chat.completions.create(
            model=self.endpoint.model, max_tokens=self.max_tokens,
            temperature=QWEN_NON_THINKING['temperature'], top_p=QWEN_NON_THINKING['top_p'],
            timeout=self.timeout, response_format=_SCHEMA,
            extra_body={'chat_template_kwargs': {'enable_thinking': False},
                        'top_k': QWEN_NON_THINKING['top_k']}, messages=[{
                'role': 'user', 'content': [
                    {'type': 'text', 'text': (
                        'Answer the narrow question about this course image. '
                        'Describe only what is visibly supported; report unreadable labels, '
                        'ambiguous arrows and uncertainty. No course-wide inferences. '
                        'Return JSON with observation and uncertainties. Question: ' + question)},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}}
                ]}])
