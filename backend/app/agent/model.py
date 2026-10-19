"""The research model client: one OpenAI-compatible chat call with native tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from openai import AsyncOpenAI

from backend.app.services.model_routing import Endpoint


# Sampling for the research and vision calls. QWEN_NON_THINKING, Qwen's advice for
# non-thinking mode, is the default: it avoids the endless repetition greedy
# decoding can fall into. GREEDY gives the same reply to the same input, for
# benchmark runs that must repeat an older one.
GREEDY = {'temperature': 0}
QWEN_NON_THINKING = {'temperature': 0.7, 'top_p': 0.8, 'top_k': 20}


def sampling_request(sampling: dict | None) -> tuple[dict, dict]:
    """The OpenAI arguments and the extra request body of a sampling setting
    (QWEN_NON_THINKING when None). top_k is not an OpenAI parameter; vLLM reads
    it from the body."""
    arguments = dict(QWEN_NON_THINKING if sampling is None else sampling)
    top_k = arguments.pop('top_k', None)
    return arguments, ({} if top_k is None else {'top_k': top_k})


@dataclass
class ModelReply:
    content: str
    tool_calls: list[dict] = field(default_factory=list)  # [{'id', 'name', 'arguments' (JSON text)}]
    model: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None


class ResearchModel:
    """Sends the conversation, and the tools when given, to the configured endpoint.

    The caller passes the tool list on each call: without it the model can only
    write text, which is how the research runner withdraws the tools.
    """

    def __init__(self, endpoint: Endpoint, *, sampling: dict | None = None):
        if not endpoint.base_url or not endpoint.model:
            raise ValueError('model_unavailable')
        self.endpoint = endpoint
        self.sampling, self.extra_body = sampling_request(sampling)
        self.client = AsyncOpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key or 'local',
                                  max_retries=0)

    async def __call__(self, messages: list[dict], max_output: int, timeout: float, *,
                       tools: list[dict] | None = None, purpose: str = '',
                       on_text: Callable[[str], None] | None = None) -> ModelReply:
        # purpose (agent, answer, citation_correction) is for usage accounting
        # by a billing wrapper; the call itself does not depend on it.
        # on_text, when given, receives the visible text as it is generated
        # (a draft: the caller still checks the whole reply).
        request = dict(
            model=self.endpoint.model, messages=messages, **self.sampling,
            max_tokens=max_output, timeout=timeout,
            extra_body={'chat_template_kwargs': {'enable_thinking': False}, **self.extra_body},
            **({'tools': tools} if tools else {}))
        if on_text is None:
            response = await self.client.chat.completions.create(**request, stream=False)
            message = response.choices[0].message if response.choices else None
            calls = [{'id': c.id, 'name': c.function.name, 'arguments': c.function.arguments}
                     for c in (message.tool_calls or [])] if message else []
            content = (message.content or '') if message else ''
            model, usage = response.model, response.usage
        else:
            content, calls, model, usage = await self._stream(request, on_text)
        # Some compatible servers put a thinking preamble in the visible text.
        # Never pass it on to the student or back into the conversation.
        if '</think>' in content:
            content = content.rsplit('</think>', 1)[1].strip()
        if '<think>' in content:
            content = ''
        return ModelReply(content, calls, model or self.endpoint.model,
                          usage.prompt_tokens if usage else None,
                          usage.completion_tokens if usage else None)

    async def _stream(self, request: dict, on_text: Callable[[str], None]):
        """One streamed call: the text goes to on_text as it arrives; tool calls
        are assembled from their fragments."""
        stream = await self.client.chat.completions.create(
            **request, stream=True, stream_options={'include_usage': True})
        text, calls, model, usage = '', {}, '', None
        async for chunk in stream:
            model = chunk.model or model
            usage = chunk.usage or usage
            for choice in chunk.choices:
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    # A thinking preamble is never shown, not even as a draft.
                    if '<think>' not in text:
                        on_text(delta.content)
                for part in delta.tool_calls or []:
                    call = calls.setdefault(part.index, {'id': '', 'name': '', 'arguments': ''})
                    call['id'] = call['id'] or part.id or ''
                    if part.function:
                        call['name'] = call['name'] or part.function.name or ''
                        call['arguments'] += part.function.arguments or ''
        return text, [calls[i] for i in sorted(calls)], model, usage
