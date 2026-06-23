"""
test_stress_load.py — [v7.1] Phase 5.1 concurrency / latency stress test.

⚠️  THIS TALKS TO A REAL INFERENCE ENGINE — it is NOT part of the normal suite.

It auto-skips unless you opt in:

    STRESS_TEST=1 \
    LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen3 \
    .venv/bin/python -m pytest backend/tests/test_stress_load.py -s

Rationale (V7.1_PLAN §5.1): 60 tps is the single-request number; self-eval multiplies
total token volume, so the real question is whether 20 concurrent requests stay within
the KV / latency budget. The thresholds below are deliberately loose — tune them to your
hardware; a failure here is a signal to investigate, not a unit-test regression.
"""

import asyncio
import os
import time

import pytest

_ENABLED = os.environ.get("STRESS_TEST") == "1"
pytestmark = pytest.mark.skipif(not _ENABLED, reason="set STRESS_TEST=1 to run (needs a live engine)")

# Tunables (override via env to match your box).
CONCURRENCY = int(os.environ.get("STRESS_CONCURRENCY", "20"))
MAX_TOKENS = int(os.environ.get("STRESS_MAX_TOKENS", "128"))
P95_BUDGET_S = float(os.environ.get("STRESS_P95_BUDGET_S", "30"))
PROMPT = "Explain mutual exclusion in one short paragraph."


@pytest.mark.asyncio
async def test_twenty_concurrent_requests_within_latency_budget():
    from openai import AsyncOpenAI

    base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    api_key = os.environ.get("LLM_API_KEY", "ollama")
    model = os.environ.get("LLM_MODEL", "qwen3")

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def one_request() -> tuple[float, int]:
        start = time.perf_counter()
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=MAX_TOKENS,
        )
        elapsed = time.perf_counter() - start
        completion = (resp.usage.completion_tokens if resp.usage else MAX_TOKENS)
        return elapsed, completion

    wall_start = time.perf_counter()
    results = await asyncio.gather(*(one_request() for _ in range(CONCURRENCY)))
    wall = time.perf_counter() - wall_start

    latencies = sorted(r[0] for r in results)
    total_tokens = sum(r[1] for r in results)
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    aggregate_tps = total_tokens / wall if wall else 0.0

    print(
        f"\n[stress] concurrency={CONCURRENCY} wall={wall:.1f}s "
        f"p95_latency={p95:.1f}s aggregate_tps={aggregate_tps:.1f} "
        f"total_tokens={total_tokens}"
    )

    # All requests succeeded and the tail latency stayed within budget.
    assert len(results) == CONCURRENCY
    assert p95 <= P95_BUDGET_S, f"p95 latency {p95:.1f}s exceeds budget {P95_BUDGET_S}s"
