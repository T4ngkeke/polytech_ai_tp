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
  comparable with the old ones. With 8 and 2 (the defaults until 2026-10-13) the prompt
  is unchanged.
- The benchmark runs without a reranker. The student chat uses one when it is
  configured.
- About 26% of `view_image` calls failed (434 recorded calls). Causes: a
  fixed 18 s time limit while the GPU was shared (13%); good replies refused
  because one uncertainty was over 250 characters or the observation over
  1200 (11%); replies cut at 700 tokens (2%). Every failure reached the model
  as `model_unavailable`, so it repeated the same broad question. Fixed on
  2026-10-12 (see below).
- The model server must support native tool calls (for vLLM:
  `--enable-auto-tool-choice` and a `--tool-call-parser`). In the experiments,
  Ornith ran without them and its tool calls were parsed by the DSH gateway,
  which is no longer part of the code.

## Model input changes after the benchmark (2026-10-12)

The benchmark runs above used tool names with the prefix `mcp__courses__`,
appended a note to the system message when the tools were withdrawn, and sent
chat history as JSON inside one user message. Now the tool names have no
prefix, the system message never changes, history is sent as real turns
(without the old [E#] IDs), and an answer may cite several IDs in one
bracket ([E1, E2]). The "selected source" for a follow-up (a clicked citation
named to the model) was removed the same day: the click was invisible to the
student, and opening a PDF page is not a request to ask about it.

Check on Qwen3.8-27B (W03, W07, W12, 2 rounds, relaxed_slow, before vs after,
`m4-agent/2026-10-12-model-input-27b`): answer points 16/18 before, 17/18
after; pages 12/18 and 14/18. Six trials per side, so this shows no
regression, not an improvement. The benchmark questions have no history; the
follow-up path was checked with the real-model chat test.

## Prompt: citations and handles (2026-10-12)

The end-to-end test showed 27B writing handles to the student ("pages 22-23
du cours (d12)"). The prompt now says each [E#] is the link the student sees,
that earlier answers carry no valid IDs, and never to write handles. A first
wording ("name the file and page") made the model drop [E#] entirely (0
citations in 8 runs); the committed wording keeps them. Check on 27B (W03,
W07, W12, 2 rounds, `m4-agent/2026-10-12-prompt-v2-27b`): handles in answers
6 → 0, answers without citations 1 → 0, answer points about 16/18 (17/18
before; the difference is W12's "a view is a virtual table"). A follow-up
after an earlier answer still gets no citations: the model copies the
earlier answer, whose [E#] IDs are removed from the history. This needs a
code change, not a prompt change.

## view_image fix (2026-10-12)

The vision call now uses the run's call time (`AGENT_CALL_SECONDS`) and up to
1200 output tokens, shortens long parts instead of refusing the reply, and
reports `vision_timeout`, `vision_bad_output`, `vision_unreachable` or
`image_unavailable`. Failed calls no longer use the visual budget. Research
and vision use the same model (the evaluator's `M4_VISION_URL` defaults to
`M4_UPSTREAM`).

Check on Qwen3.8-27B for research and vision (W08, W03, W02, 2 rounds,
3 in flight, relaxed_slow, `m4-agent/2026-10-12-view-image-27b`): vision calls
succeeded 4/9 before and 7/7 after; all failures before were the 18 s
time-out. Pages 16/18 on both sides; W03 answers were complete on both sides.

## End-to-end test (2026-10-12)

A temporary stack (own database, the 14-PDF corpus loaded through the
product's upload and publish path, Qwen3.8-27B for research, answers and
vision) was driven in headless Firefox: log in, ask W03, open a citation,
ask a follow-up, reload, say thanks. What it found and what changed:

- A fresh backend image did not start (SQLAlchemy without greenlet); fixed.
- Follow-ups had no citations (see the prompt section); fixed by numbering
  evidence across the session. Follow-up after W03, 2 runs: 5 citations
  each, all on the page asked about.
- nginx closed the stream after 60 s without data while 27B wrote a long
  answer, and the answer was lost. The backend now sends an SSE comment
  every 15 s, and nginx waits up to 600 s.
- After that disconnect the run stayed 'cancelling', which blocks its
  session until the stale-run timeout. The two cancel steps now run in one
  shielded task.
- Query embedding on the CPU took 0.25 s with spikes of 8-11 s; on the GPU
  0.03 s with the same vectors.
- Switching to another browser tab while waiting lost the answer (the
  stream library closes hidden streams and resends them). Fixed in the chat
  and by letting a duplicate request wait for its run.
- Student questions (replayed): a vague "我不太理解automate" read the chapter
  page by page until the 20-call limit; a follow-up answered from the
  history cited earlier IDs, which session numbering had made invalid, and
  the whole answer was replaced by a failure message. Now an answer may cite
  earlier answers' IDs (validated like new ones), and the prompt asks for
  an overview from two to four pages on a broad question. Replay on 27B:
  vague question 44 s with 8 citations (was 69 s, call limit); follow-up
  80 s with 7 citations on the right pages; a one-sentence summary 3 s,
  citing two earlier IDs. Benchmark W03/W07/W12 x 2: 6.3 tool calls per
  question (was 9.3), no call limit reached, about the same answer points.
- Each tool call is now logged (backend.app.agent.runner, INFO).

## Replies without research (2026-10-13)

In the end-to-end chat, Qwen3.8-27B sometimes ended a run without any tool
call: a promise ("Laissez-moi la lire"), a sentence repeated for 830 tokens,
or a made-up line ("[assistant stopped: content filter false positive]").
Asked to summarise a page, it also wrote the summary from memory with
invented [E#] IDs; the correction turn after the loop has no tools, so the
answer became an apology or the failure text. Replayed on the e2e stack
(4 conversations of 4 questions), 2 of 4 page-summary answers failed this
way, and one read page 23 of another course: the model guessed that d1 was
the course asked about (handles are not explained before a search; not
changed).

The runner now adds two turns, each at most once per run:

- A reply that cites IDs nothing returned, while the tools are still open,
  is sent back with "read the pages you need, then answer"; the correction
  without tools stays for the end of the research.
- A reply with no tool call in the run and no citation gets a check: the
  model reads or cites now, or answers DONE and the reply is kept. A
  greeting or a thanks costs one short extra call (5-7 s in all on 27B).

Replay after the change (4 conversations, a thanks added): 16 of 16 course
answers cite pages, the page-23 summaries read page 23, and no answer
mentions the check. Benchmark W03/W07/W12 x 2 (`m4-agent/2026-10-13-answer-check-27b`):
pages 14/18 (10/18 in the 2026-10-13 baseline run), no partial answer; neither
turn was needed. Calls use temperature 0, which Qwen advises against (it can
repeat endlessly); changing it would change every benchmark and is not done.

## File list, sampling and model (2026-10-13)

Before a search the model did not know which handle is which file, and it
sometimes guessed (d1.p23 for "page 23 of the theory course" read another
course). The system message now lists the lab's files with their handles and
page counts (at most 40 lines; the same text for every run of a lab, so the
prefix stays cached).

Calls use greedy decoding (temperature 0). Qwen advises temperature 0.7,
top_p 0.8, top_k 20 for non-thinking mode, because greedy decoding can repeat
endlessly (seen once on 27B: one sentence for 830 tokens). The evaluator can
now run both (`M4_SAMPLING=greedy|qwen`). On 2026-10-14 the product and the
evaluator moved to Qwen's settings (`M4_SAMPLING=greedy` repeats older runs).

Benchmark v4, 17 questions x 2 rounds, relaxed_slow, 3 in flight, all with the
file list (`m4-agent/2026-10-13-cmp-*`; pages computed by the scorer, not a
content review; times are indicative: Flash-Next also served the v6 beta):

| Model, sampling | Pages | Complete questions | Citation precision | Partial | Median / p95 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 27B, no file list (baseline of the same day) | 74/106 (70%) | 11/34 | 69% | 0 | 54 / 119 |
| 27B, greedy | 77/106 (73%) | 13/34 | 80% | 0 | 61 / 124 |
| 27B, Qwen sampling | 83/106 (78%) | 16/34 | 76% | 0 | 72 / 146 |
| Flash-Next, greedy | 91/106 (86%) | 23/34 | 71% | 1 | 79 / 190 |
| Flash-Next, Qwen sampling | 94/106 (89%) | 22/34 | 68% | 0 | 80 / 164 |

No answer repeated text in any of the 152 trials. Flash-Next reads more
(about 10.5 tool calls per question against 8.9 on 27B) and finds more pages.
The one partial answer (Flash-Next greedy, W17) was a good answer with a
malformed reference: the correction turn has no tools, the model wrote a
tool call, and the whole answer was replaced. Since then an unusable
correction keeps an answer whose IDs were all valid. The sampling difference
is within one run's spread (one question can move by a facet between rounds).
On 2026-10-13 the user moved every environment (v6 beta, dev, e2e) to
Flash-Next.

## Citation precision of Flash-Next (2026-10-14)

Citation precision counts a cited page as good only when the benchmark lists
it (a facet's acceptable pages or the question's related pages). Flash-Next
cites more pages per run than 27B (163 and 177 against 110 and 126) and more
listed pages too (116 and 120 against 88 and 96). Of its unlisted pages, most
are within two pages of a listed one (22 of 47 greedy, 36 of 57 with Qwen
sampling); 7 to 10 are in another file.

A check of 24 unlisted citations, chosen at random from both Flash-Next runs,
against the page text: 22 support the sentence that cites them (other
examples on the same subject, the next slide of the same section, a page with
the same text as the listed one). One is a title page and one is unclear.
The lower precision is mostly pages the benchmark does not list, not wrong
citations. No change to the product. Precision on this benchmark should be
read with a content check; adding these pages to the lists after seeing the
answers would favour this model.

## Reranker (2026-10-14)

The search tool can rerank its fused candidates (about 40) with a cross
encoder before it keeps the top ones. BAAI/bge-reranker-v2-m3 runs on vLLM
(`edullm-reranker`, about 2.8 GB next to Flash-Next) and the evaluator takes
`M4_RERANK_URL`. Benchmark v4, 17 questions x 2 rounds, Flash-Next, Qwen
sampling, the same day and setup for both runs
(`m4-agent/2026-10-14-rerank-off`, `-on`):

| Reranker | Pages | Complete questions | Citation precision | Zero-citation | Median / p95 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| off | 90/106 (85%) | 23/34 | 70% | 1 | 84 / 190 |
| on | 93/106 (88%) | 22/34 | 75% | 0 | 81 / 154 |

99 rerank calls, none failed, 0.16 s each. Tool calls per trial 10.7 against
11.1. Pages and complete questions move within one run's spread (nine
questions changed by one facet or more, in both directions); citation
precision gains five points. The reranker is on in the v9 test stack.

```sh
docker run -d --name edullm-reranker --gpus all --ipc=host --restart unless-stopped \
  --network edullm-models -p 127.0.0.1:18004:8000 \
  -v /home/spark-1/.cache/huggingface:/root/.cache/huggingface \
  nvcr.io/nvidia/vllm:26.07-py3 \
  vllm serve BAAI/bge-reranker-v2-m3 --host 0.0.0.0 --port 8000 \
  --runner pooling --gpu-memory-utilization 0.03 --max-model-len 8192 --max-num-seqs 32
```

Admin setting: `RERANK_URL=http://edullm-reranker:8000/rerank`.

## Tool changes and answer drafts (2026-10-14)

Tool calls of the last 150 trials (Flash-Next): read 1757, search 685,
view_image 166, grep 122, open 20 (no errors), navigate 3. Argument errors:
grep without a document (9), read with a redundant `document` (2). The model
already sends two reads in one turn in most turns (305 of 517), so a
multi-page read was not added.

- grep without a document searches every document of the run, as one tool
  call: the first 20 matches in file order, and which files have more.
- read accepts `document` when it is the page's or block's own, and a block
  with its own page (`block="d4.p9.b3", page="d4.p9"`).
- navigate is gone from the model's tools (the reader endpoint stays).

Benchmark v4, 17 questions x 2 rounds, Flash-Next, reranker on, against the
reranker run of the same day (`m4-agent/2026-10-14-tools-grep-all`):

| Tools | Pages | Complete questions | Citation precision | Tool calls / trial | Median / p95 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| before | 93/106 (88%) | 22/34 | 75% | 10.7 | 81 / 154 |
| after | 96/106 (91%) | 24/34 | 80% | 9.7 | 77 / 143 |

The model now greps more (81 calls, 44 over all files) and searches less (44
against 100). Four read errors of that run were a block given with its own
page, accepted since.

Answer drafts: the calls that may write the answer (`agent`, `answer`) are
streamed when the chat passes `on_draft`; the SSE stream sends the text as
`draft` events and `draft_reset` when the model goes on researching; the
checked answer still ends the stream. In the reranker run the last model call
took a median 38 s of 75 s. On the v9 stack a WHERE/HAVING question showed its
first draft text at 69 s and the checked answer at 118 s, the same text. The
benchmark does not stream (no `on_draft`).

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
