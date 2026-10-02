"""M2-C: model descriptions of pictures, as candidates only.

Nothing here runs by itself. A teacher asks for one description of one block.
The model is used only when all of these are true:

* STRUCTURED_VISION_ENABLED is on. An operator sets it only after checking
  that the configured model really reads images (the M0 probe found the
  deployed VLM_MODEL empty, and a text model must never get a picture);
* the SystemConfig vision slot (VLM_BASE_URL / VLM_API_KEY / VLM_MODEL) names
  a model. No port, host or service is written into this code.

The result is stored as a pending review with the model, prompt version,
time and usage. Missing usage is marked "missing", never guessed. The cost
is ingestion cost: nothing here touches a student's quota. The description can
only ever become the block's description, never its content, so native code
and tables are not replaced by what a model saw.
"""
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from backend.app.config import settings

PROMPT_VERSION = 'm2c-figure-description-1'
MAX_DESCRIPTION = 400
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_MIMES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')

PROMPT = (
    "You see one picture from a course slide. Write a short factual description that helps a "
    "student find and understand it: what kind of picture it is and what it shows. At most 350 "
    "characters, in the language of the text on the picture. Do not guess what the picture is "
    "used for in the course. Do not state the direction of an arrow, an order or a relation "
    "unless it is plainly drawn; list anything you are not sure about in 'uncertainties'."
)
RESPONSE_FORMAT = {
    'type': 'json_schema',
    'json_schema': {'name': 'figure_description', 'strict': True, 'schema': {
        'type': 'object', 'additionalProperties': False, 'required': ['description', 'uncertainties'],
        'properties': {'description': {'type': 'string'},
                       'uncertainties': {'type': 'array', 'items': {'type': 'string'}}}}},
}


class VisionUnavailable(RuntimeError):
    """No verified vision model is configured. Nothing was called."""


class VisionRejected(ValueError):
    """The model answered, but the answer cannot be kept. The message is a code."""


@dataclass
class VisionResult:
    text: str
    model: str
    latency_ms: int
    usage: dict | None
    uncertainties: list = field(default_factory=list)
    asset_id: object = None


DescribeFn = Callable[[bytes, str, dict], Awaitable[VisionResult]]


def check_result(result: VisionResult) -> VisionResult:
    text = (result.text or '').strip()
    if not text:
        raise VisionRejected('empty_description')
    if len(text) > MAX_DESCRIPTION:
        # Refused, not cut: a cut sentence can say something else.
        raise VisionRejected('description_too_long')
    result.text = text
    return result


def build_describe_fn(configs: dict) -> DescribeFn:
    """The configured client, or VisionUnavailable. Called per request."""
    from backend.app.services.model_routing import resolve_model_routing

    if not settings.STRUCTURED_VISION_ENABLED:
        raise VisionUnavailable('structured_vision_disabled')
    endpoint = resolve_model_routing(configs).vlm
    if not endpoint.model:
        raise VisionUnavailable('vision_model_not_configured')

    async def describe(image: bytes, mime: str, context: dict) -> VisionResult:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)
        url = f'data:{mime};base64,{base64.b64encode(image).decode()}'
        started = time.monotonic()
        response = await client.chat.completions.create(
            model=endpoint.model, max_tokens=1200, response_format=RESPONSE_FORMAT,
            messages=[{'role': 'user', 'content': [
                {'type': 'text', 'text': PROMPT + (f"\nSlide title: {context['title']}" if context.get('title') else '')},
                {'type': 'image_url', 'image_url': {'url': url}}]}])
        latency = int((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(response.choices[0].message.content or '')
            text, uncertainties = payload['description'], list(payload.get('uncertainties') or [])
        except (ValueError, KeyError, TypeError, IndexError):
            raise VisionRejected('invalid_model_answer') from None
        usage = None
        if response.usage is not None:
            usage = {'input_tokens': response.usage.prompt_tokens, 'output_tokens': response.usage.completion_tokens,
                     'source': 'reported'}
        return VisionResult(text=text, model=response.model or endpoint.model, latency_ms=latency,
                            usage=usage, uncertainties=uncertainties)

    return describe
