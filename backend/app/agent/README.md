# The chat agent

This folder holds the code that answers every student chat. There is one path: the research loop (`runner.py`). The model decides by itself whether to search the course: a greeting needs no tool, a course question gets searched. The student may turn on deep research, which gives the loop more room (more tool calls and more time). Each answer records how it used the course (`result.course_use`: `cited`, `not_found` or `none`).

Exam mode: the loop sees only the files the teacher allowed for the exam. With none, it has no tools at all and answers from general knowledge (stop reason `exam_no_files`).

Until 2026-11-26 there were three answer modes: Direct (one answer without the course), Search (one search, then one answer) and Research. The new interface sent Research only, so `direct.py`, `search.py` and the TP text they read (`tp_text.py`) were removed. Old answers keep their mode in `messages.answer_mode`.

How it works, and why, is in [documentation/ARCHITECTURE.md](../../../documentation/ARCHITECTURE.md).

## Files

The folder has two subfolders (since 2026-11-26): `tools/` and `context/`. The main loop and what every part shares stay at the top.

### The main loop

| File | What it does |
| --- | --- |
| `runner.py` | The research loop. It keeps the limits (tool calls, time, input room). When a limit is reached, it removes the tools and asks for the answer. It checks the cited `[E#]` IDs, and cleans references such as `[E21 in d1.p21]` without a model call. |
| `evidence.py` | Evidence IDs (`E1`, `E2`, ...) and their sources. Only pages that were read count as evidence. Search results are leads. |
| `result.py` | The run result, the citation patterns, the stop exceptions, the usage entry of a model call (`call_usage`) and the final check of the cited documents (`cited_sources`). |
| `model.py` | The model client: one OpenAI-compatible chat call with native tools, and the sampling settings. |

### `tools/`: what the model can call

| File | What it does |
| --- | --- |
| `tools/course.py` | The course tools as the model sees them: `search`, `open`, `read`, `grep`, `view_image` (`ask_student` is in `runner.py`). It gives short handles (`d1`, `d1.p20`, `d1.p20.b3`, `a2`, `c1`), keeps the tool budget and registers evidence. |
| `tools/access.py` | Access checks and reading of the published course PDFs. The scope and the document revisions are fixed by the server. `check_access` checks that the session is still open to the student and that each document is still readable at its fixed revision; every tool call and the final citation check go through it. |
| `tools/web.py` | Web search, when the student turns it on and the lab allows it: `search_web` (a SearXNG instance, `WEB_SEARCH_URL`) and `fetch_url`, ported from Open WebUI with its address checks (only public addresses, checked again at connect time). Results and pages get IDs `W1`, `W2`; the answer cites them as `[W#]`, apart from the course `[E#]`. Own budget of 8 calls. |
| `tools/code.py` | Python, when the student turns it on and the lab allows it: `execute_code`, ported from Open WebUI. With `PYTHON_RUNNER_URL` the code runs on the server (`python_runner/`: Pyodide, one process per run, no network, 60 s); the chat stream sends a `code` event so the page shows the code, then `code_output`. Without it, the code runs in the student's browser: the page posts the output back within 240 s (`CODE_TIMEOUT`). Own budget of 5 runs, each starting afresh on the server; the wait is not research time. Output images become chat files; each run is saved on the answer, with `where`. |
| `tools/tasks.py` | The task checklist the model may keep during a research run: `create_tasks` and `update_task`, ported from Open WebUI (see `third_party/open-webui/`). They do not use the course tool budget; each change is sent to the page and the final list is saved with the answer. |

### `context/`: what goes into the prompt besides the question

| File | What it does |
| --- | --- |
| `context/history.py` | Which earlier messages are sent: a share of the model's context (`HISTORY_CONTEXT_PERCENT`), with fixed start points so that the model server can reuse its cache. |
| `context/compaction.py` | Context compaction, ported from Open WebUI: when older messages no longer fit the history (`context/history.py`), one call summarizes them with the previous summary; the summary is stored on the first message still sent and goes in front of it, reused until the start jumps again (the prompt start stays the same). Only the safe history; a summary is reused only with the same course file revisions; none in exam mode. `CONTEXT_COMPACTION_ENABLED` (on). |
| `context/memory.py` | The student's notes of a class (`services/memory_service.py`), ported from Open WebUI: the notes that fit go at the end of each research question (`<memory_context>`, 2000 characters at most), and a background review every 4 student turns adds, rewrites or removes notes (learning preferences and progress only). Not in exam mode, in a test drive, or when the student turned memory off. The review is not charged to the student. |
| `context/attachments.py` | What a question attaches, as the model receives it: images as image parts of the question, a PDF or text file as text (20,000 characters at most), an earlier chat as text; a scanned PDF asks for a screenshot; an attached course file limits the search. Never cited as `[E#]`. |
| `context/language.py` | One small model call that names the language of the answer, from the teacher's rules and the student's last messages. |

### After the answer

| File | What it does |
| --- | --- |
| `visuals.py` | Diagrams and charts in answers, in two fixed formats only: a ```` ```mermaid ```` block or a ```` ```chart ```` block of checked JSON (`Chart`: bar, line, scatter or pie; labels and series). Every prompt names them (`VISUAL_NOTE`); before an answer is saved, a block that does not pass the check becomes plain code, so the page never renders free HTML or JavaScript. |
| `follow_ups.py` | Suggested next questions under an answer, ported from Open WebUI: one call after the answer (first question + last 4 messages), with the teacher's rules; at most 5, saved on the answer and sent as a `follow_ups` event. Not in exam mode, not after a question back, not charged to the student. |

## Where it is called

`routers/chat.py` checks the lab, the membership, the quota and the tools allowed, and calls `run_persisted_research` in `services/research_service.py`. That service saves the answer with its citations, bills each model call and sends the server-sent events.

## Models

The agent uses the main model, the same one as `view_image` and the language call. The model slots are resolved in `services/model_routing.py`, and the HTTP clients are shared per endpoint (`services/shared_clients.py`). The search tool uses the embeddings and the reranker through `services/retrieval_service.py`.

## Tests

Tests are in `backend/tests/` (for example `test_m4_*`, `test_runner.py`, `test_course_tools.py`, `test_history_trim.py`). Run them with `.venv/bin/python -m pytest backend/tests -q`. The language call is off in the tests unless a test turns it on (`conftest.py`), so the model replies a test scripts come in the expected order.
