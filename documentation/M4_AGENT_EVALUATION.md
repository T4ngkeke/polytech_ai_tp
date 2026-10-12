# M4 research agent: evaluation

This note explains why the research agent (`backend/app/agent/runner.py`) has
its current form, and how to run the benchmark again.

## What was compared (2026-10-02 to 2026-10-09)

- Agent loops: the first JSON planner with a separate answer call, a repaired
  version of it, a version with native tool calls and a `finish` tool,
  DeepSeek Harness (DSH, an agent SDK), and a single-conversation loop
  written by hand. The single-conversation loop is now `runner.py`.
- Baseline "strong RAG": one search with the raw question; the model reads the
  full text of the found pages and of their adjacent pages, then answers.
- All of them used the same course tools, limits and corpus.
- The code of every variant is kept on the branch
  `archive/m4-agent-experiments-2026-10-09` (commit `f83f89f`). It is not merged.
- The data is local, under `documentation/ingestion-agent-acceptance/` (not in
  git): the frozen corpora (4 PDFs; corpus v3 with 14 PDFs and 507 pages), the
  frozen benchmarks (v2: 28 questions, v3: 27, v4: 17 questions with 53
  facets) and every run output.

## Results that decided the design

Benchmark v4, model Qwen3.8-Flash-Next (v0.30), round 1:

| Loop | Content score | Facets with a supporting page cited |
| --- | --- | --- |
| Single conversation (now `runner.py`) | 104 / 106 | 46 / 53 |
| DSH | 103 / 106 | 45 / 53 |
| Repaired JSON planner | 89 / 106 | |

- The single loop and DSH give the same quality. DSH was better than the
  earlier loops because of its structure: the model that answers is the one
  that searched, it decides when it has read enough, and it sees everything it
  read. The SDK itself added nothing.
- Speed is the same. Median seconds per question with 1 and 3 questions in
  flight (3 questions, 2 rounds): DSH 51 and 84, single loop 49 and 82.
- DSH adds two processes per session (about 300 MB); the single loop adds none.
- The model matters more than the loop. With the same loops, content scores
  were about 65–79% with Ornith-1.5-35B-A3B, 92% with Qwen3.8-27B and 97–98%
  with Flash-Next.
- Median seconds per question for the single loop on Flash-Next, with 1, 2, 3
  and 4 questions in flight: 50, 60, 82 and 116.

## Limits of these results

- The content of the answers was reviewed by the assistant (Claude), not by a
  person.
- Strong RAG always used the top 6 search results: the old evaluator forced
  `top_k=6` for every tool call, also under the "relaxed" budget that asked
  for 10. `scripts/m4_evaluate.py` no longer does this, so new relaxed
  strong-RAG runs are not comparable with the old ones.
- The system prompt said "at most eight course tool calls and two visual
  calls" in every run, also under the relaxed budgets (20 and 4 calls). The
  prompt now states the run's real limits, so new relaxed runs are not
  comparable with the old ones. With the default limits (8 and 2) the prompt
  is unchanged.
- The benchmark runs without a reranker. The student chat uses one when it is
  configured.
- About 23% of `view_image` calls failed (time-outs under load, output limits).
- The model server must support native tool calls (for vLLM:
  `--enable-auto-tool-choice` and a `--tool-call-parser`). In the experiments,
  Ornith ran without them and its tool calls were parsed by the DSH gateway,
  which is no longer part of the code.

## Model input changes after the benchmark (2026-10-12)

The benchmark runs above used tool names with the prefix `mcp__courses__`,
appended a note to the system message when the tools were withdrawn, and sent
chat history as JSON inside one user message. Now the tool names have no
prefix, the system message never changes, history is sent as real turns
(without the old [E#] IDs), a selected source is named by its handle, and an
answer may cite several IDs in one bracket ([E1, E2]).

Check on Qwen3.8-27B (W03, W07, W12, 2 rounds, relaxed_slow, before vs after,
`m4-agent/2026-10-12-model-input-27b`): answer points 16/18 before, 17/18
after; pages 12/18 and 14/18. Six trials per side, so this shows no
regression, not an improvement. The benchmark questions have no history; the
follow-up path was checked with the real-model chat test.

## Running the benchmark

`backend/tests/test_m4_evaluate_pg.py` builds the frozen corpus in a disposable
PostgreSQL database (`TEST_PG_URL`, a database whose name ends in `_test`; the
fixture drops all tables) and runs `scripts/m4_evaluate.py`. Settings are
environment variables, listed at the top of that script. Example:

```
TEST_PG_URL=postgresql+asyncpg://...:55434/scratch_test \
M4_EVAL_OUTPUT=/path/to/output M4_EMBED_URL=http://<bge-m3 host>:11434/api/embed \
M4_CORPUS=v3 M4_BENCHMARK=v4 M4_SPLIT=test M4_BUDGET=relaxed_slow \
M4_UPSTREAM=http://127.0.0.1:18301/v1 M4_MODEL_LABEL=Flash-Next M4_CONCURRENCY=3 \
.venv/bin/python -m pytest -s backend/tests/test_m4_evaluate_pg.py

.venv/bin/python scripts/m4_v2_score.py /path/to/output
```
