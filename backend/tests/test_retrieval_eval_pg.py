"""Opt-in export for scripts/retrieval_eval.py on a disposable PostgreSQL database.

  TEST_PG_URL=... RETRIEVAL_EVAL_OUTPUT=out M4_EMBED_URL=http://<bge-m3>/api/embed \
  .venv/bin/python -m pytest -s backend/tests/test_retrieval_eval_pg.py
"""
import os
from pathlib import Path

import httpx
import pytest


@pytest.mark.asyncio
async def test_retrieval_eval_export(pg_session, tmp_path, monkeypatch):
    if not os.getenv('RETRIEVAL_EVAL_OUTPUT'):
        pytest.skip('Set RETRIEVAL_EVAL_OUTPUT to export the retrieval comparison')
    from backend.tests.test_m4_tools_corpus_pg import build_corpus_v3
    from scripts.retrieval_eval import export
    url = os.environ['M4_EMBED_URL']
    await build_corpus_v3(pg_session, tmp_path, monkeypatch, embed_url=url)
    async with httpx.AsyncClient(timeout=60) as client:
        async def embed(text):
            response = await client.post(url, json={'model': 'bge-m3:latest', 'input': text})
            response.raise_for_status()
            return response.json()['embeddings'][0]
        print(await export(pg_session, Path(os.environ['RETRIEVAL_EVAL_OUTPUT']).resolve(), embed))
