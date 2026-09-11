"""Audit-only embedding reuse preserves request order and duplicate inputs."""
import pytest
from scripts.check_worker_visual_index import cached_embed


@pytest.mark.asyncio
async def test_reuse_only_exact_text_and_preserve_order():
    calls=[]
    async def remote(texts):
        calls.append(texts)
        return [[2.0] for _ in texts]
    embed=cached_embed(remote,{'same text':[1.0]})
    assert await embed(['new text','same text','new text'])==[[2.0],[1.0],[2.0]]
    assert calls==[['new text']]
    await embed(['new text'])
    assert len(calls)==1


@pytest.mark.asyncio
async def test_bad_embedding_count_does_not_poison_cache():
    cache={}
    async def remote(texts):return [[1.0]]
    with pytest.raises(ValueError):await cached_embed(remote,cache)(['one','two'])
    assert cache=={}


def test_region_audit_cannot_bypass_unimplemented_review_persistence():
    from scripts.check_worker_visual_index import validate_audit_protocol
    with pytest.raises(ValueError,match='review persistence'):
        validate_audit_protocol([{'audit':{'version':'region-reader-v1.1','reviews':[]}}])
    validate_audit_protocol([{'audit':{'version':'visual-harness-v4.2'}}])
