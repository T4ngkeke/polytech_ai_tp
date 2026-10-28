"""Opt-in benchmark run of scripts/m4_evaluate.py on a disposable PostgreSQL database.

Example (Flash-Next on port 18301, benchmark v4 test split, corpus v3):
  TEST_PG_URL=... M4_EVAL_OUTPUT=out M4_EMBED_URL=http://<bge-m3>/api/embed M4_CORPUS=v3 \
  M4_BENCHMARK=v4 M4_SPLIT=test M4_UPSTREAM=http://127.0.0.1:18301/v1 \
  .venv/bin/python -m pytest -s backend/tests/test_m4_evaluate_pg.py
M4_CAPTIONS=1 builds corpus v3 with picture captions written by the evaluated model.
"""
import os
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_benchmark_run(pg_session, tmp_path, monkeypatch):
    if not os.getenv('M4_EVAL_OUTPUT'):
        pytest.skip('Set M4_EVAL_OUTPUT to run the benchmark')
    from backend.tests.test_m4_tools_corpus_pg import build_corpus_v3, test_four_pdfs_rebuild_and_tool_sources
    from scripts.m4_evaluate import evaluate
    if os.getenv('M4_CORPUS') == 'v3':
        captions = None
        if os.getenv('M4_CAPTIONS') == '1':
            # Picture captions written by the evaluated model while building the corpus.
            from backend.app.services.model_routing import Endpoint
            from backend.worker.picture_captions import CaptionSettings
            captions = CaptionSettings(Endpoint(os.getenv('M4_UPSTREAM', 'http://127.0.0.1:18301/v1'), 'local',
                                                os.getenv('M4_MODEL_NAME', 'ornith')))
        corpus = await build_corpus_v3(pg_session, tmp_path, monkeypatch,
                                       embed_url=os.environ['M4_EMBED_URL'], captions=captions)
        print('corpus-v3', corpus, flush=True)
    else:
        await test_four_pdfs_rebuild_and_tool_sources(pg_session, tmp_path, monkeypatch)
    results = await evaluate(pg_session, output=Path(os.environ['M4_EVAL_OUTPUT']).resolve(),
        storage_root=tmp_path / 'storage', embedding_url=os.environ['M4_EMBED_URL'],
        benchmark=os.getenv('M4_BENCHMARK', 'm4e'), split=os.getenv('M4_SPLIT', 'dev'),
        variants=os.getenv('M4_VARIANTS', '').split(',') if os.getenv('M4_VARIANTS') else None,
        budget=os.getenv('M4_BUDGET', 'standard'))
    assert results
