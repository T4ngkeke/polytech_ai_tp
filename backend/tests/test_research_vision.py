"""The vision client: long replies are shortened, failures have their own codes."""
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from backend.app.services.model_routing import Endpoint
from backend.app.services.research_vision import ResearchVision, VisionError


def vision_with(reply=None, *, finish='stop', error=None):
    vision = ResearchVision(Endpoint('http://vlm.invalid/v1', '', 'one-model'), timeout=30)
    async def create(*_args):
        if error:
            raise error
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(model='one-model', usage=SimpleNamespace(prompt_tokens=900, completion_tokens=300),
                               choices=[SimpleNamespace(finish_reason=finish,
                                                        message=SimpleNamespace(content=content))])
    vision._create = create
    return vision


@pytest.mark.asyncio
async def test_long_parts_are_shortened_not_refused():
    reply = await vision_with({'observation': 'x' * 2500,
                               'uncertainties': ['u' * 320] + [f'n{i}' for i in range(10)]})(b'png', 'image/png', 'Q?')
    assert len(reply.observation) == 2000 and reply.observation.endswith(' […]')
    assert len(reply.uncertainties) == 8 and len(reply.uncertainties[0]) == 300


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs, code', [
    ({'reply': {'observation': 'cut'}, 'finish': 'length'}, 'vision_bad_output'),
    ({'reply': '{"observation": "unfinished'}, 'vision_bad_output'),
    ({'reply': {'observation': '  ', 'uncertainties': []}}, 'vision_bad_output'),
    ({'error': APITimeoutError(httpx.Request('POST', 'http://vlm.invalid'))}, 'vision_timeout'),
])
async def test_failures_have_their_own_codes(kwargs, code):
    with pytest.raises(VisionError) as caught:
        await vision_with(**kwargs)(b'png', 'image/png', 'Q?')
    assert caught.value.code == code
