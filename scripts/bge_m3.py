"""One BGE-M3 call for the benchmark tools (tests and scripts/m4_evaluate.py),
which fill their vector caches with it.

M4_EMBED_URL is either an Ollama endpoint (…/api/embed, the September set-up)
or an OpenAI-style one (…/v1/embeddings, the vLLM server edullm-embeddings,
model BAAI/bge-m3, with the model servers' key in M4_EMBED_KEY).
"""
from __future__ import annotations

import os


async def embed_texts(client, url: str, texts: list[str]) -> list[list[float]]:
    if url.rstrip('/').endswith('/api/embed'):
        response = await client.post(url, json={'model': 'bge-m3:latest', 'input': texts})
        response.raise_for_status()
        vectors = response.json()['embeddings']
    else:
        key = os.getenv('M4_EMBED_KEY', '')
        response = await client.post(url, json={'model': os.getenv('M4_EMBED_MODEL', 'BAAI/bge-m3'), 'input': texts},
                                     headers={'Authorization': f'Bearer {key}'} if key else {})
        response.raise_for_status()
        vectors = [row['embedding'] for row in sorted(response.json()['data'], key=lambda row: row['index'])]
    if len(vectors) != len(texts) or any(len(v) != 1024 for v in vectors):
        raise ValueError('unexpected_embedding_answer')
    return vectors
