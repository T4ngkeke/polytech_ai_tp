"""The research model client: one OpenAI-compatible chat call with native tools."""
from __future__ import annotations

from dataclasses import dataclass, field

from openai import AsyncOpenAI

from backend.app.services.model_routing import Endpoint


# Sampling for the research calls. GREEDY gives the same reply to the same
# input (benchmarks compare runs); QWEN_NON_THINKING is Qwen's advice for
# non-thinking mode, which avoids the endless repetition greedy decoding can
# fall into.
GREEDY = {'temperature': 0}
QWEN_NON_THINKING = {'temperature': 0.7, 'top_p': 0.8, 'top_k': 20}


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
        sampling = dict(GREEDY if sampling is None else sampling)
        # top_k is not an OpenAI parameter; vLLM reads it from the request body.
        self.top_k = sampling.pop('top_k', None)
        self.sampling = sampling
        self.client = AsyncOpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key or 'local',
                                  max_retries=0)

    async def __call__(self, messages: list[dict], max_output: int, timeout: float, *,
                       tools: list[dict] | None = None, purpose: str = '') -> ModelReply:
        # purpose (agent, answer, citation_correction) is for usage accounting
        # by a billing wrapper; the call itself does not depend on it.
        response = await self.client.chat.completions.create(
            model=self.endpoint.model, messages=messages, **self.sampling,
            max_tokens=max_output, timeout=timeout, stream=False,
            extra_body={'chat_template_kwargs': {'enable_thinking': False},
                        **({'top_k': self.top_k} if self.top_k is not None else {})},
            **({'tools': tools} if tools else {}))
        message = response.choices[0].message if response.choices else None
        calls = [{'id': c.id, 'name': c.function.name, 'arguments': c.function.arguments}
                 for c in (message.tool_calls or [])] if message else []
        content = (message.content or '') if message else ''
        # Some compatible servers put a thinking preamble in the visible text.
        # Never pass it on to the student or back into the conversation.
        if '</think>' in content:
            content = content.rsplit('</think>', 1)[1].strip()
        if '<think>' in content:
            content = ''
        tokens = response.usage
        return ModelReply(content, calls, response.model or self.endpoint.model,
                          tokens.prompt_tokens if tokens else None,
                          tokens.completion_tokens if tokens else None)
