# The chat agent

This folder holds the code that answers every student chat. There is one path. The new interface has one entry: the research loop, where the model decides by itself whether to search the course (a greeting needs no tool; a course question gets searched). The student may turn on deep research, which gives the loop more room. Each answer records how it used the course (`result.course_use`: `cited`, `not_found` or `none`). The three modes below stay for the current pages. A class in exam mode answers in Direct mode only, unless the teacher allowed files for the exam.

| Mode | What happens | File |
| --- | --- | --- |
| Direct | One model call. No tools and no citations. The model sees the text of the TP files, not the class course files. | `direct.py` |
| Search | One search, then one answer from the TP text and the pages found. No tools for the model. | `search.py` |
| Research | The model works in one conversation and calls the course tools until it can answer. | `runner.py` |

How each mode works, and why, is in [documentation/ARCHITECTURE.md](../../../documentation/ARCHITECTURE.md).

## Files

| File | What it does |
| --- | --- |
| `runner.py` | The research loop. It keeps the limits (tool calls, time, input room). When a limit is reached, it removes the tools and asks for the answer. It checks the cited `[E#]` IDs. It also holds the ending of the last user message that Search and Direct share (`answer_ending`: the teacher's rules again, then the language sentence). |
| `search.py` | Search mode: the search, the pages around the best ones, the answer and its check. |
| `direct.py` | Direct mode. |
| `course_tools.py` | The tools as the model sees them: `search`, `open`, `read`, `grep`, `view_image`, and `ask_student` when offered. It gives short handles (`d1`, `d1.p20`, `d1.p20.b3`, `a2`, `c1`), keeps the tool budget and registers evidence. |
| `tools.py` | Access checks and reading of the published course PDFs. The scope and the document revisions are fixed by the server. `check_access` checks that the session is still open to the student and that each document is still readable at its fixed revision; every tool call and the final citation check go through it. |
| `tp_text.py` | The text of the TP files, read in full for Search and Direct within `TP_CONTEXT_PERCENT` of the model's context. |
| `language.py` | One small model call that names the language of the answer, from the teacher's rules and the student's last messages. |
| `history.py` | Which earlier messages are sent: a share of the model's context (`HISTORY_CONTEXT_PERCENT`), with fixed start points so that the model server can reuse its cache. |
| `memory.py` | The student's notes of a class (`services/memory_service.py`), ported from Open WebUI: the notes that fit go at the end of each research question (`<memory_context>`, 2000 characters at most), and a background review every 4 student turns adds, rewrites or removes notes (learning preferences and progress only). Not in exam mode, in a test drive, or when the student turned memory off. The review is not charged to the student. |
| `web_tools.py` | Web search, when the student turns it on and the lab allows it: `search_web` (a SearXNG instance, `WEB_SEARCH_URL`) and `fetch_url`, ported from Open WebUI with its address checks (only public addresses, checked again at connect time). Results and pages get IDs `W1`, `W2`; the answer cites them as `[W#]`, apart from the course `[E#]`. Own budget of 8 calls. |
| `task_tools.py` | The task checklist the model may keep during a research run: `create_tasks` and `update_task`, ported from Open WebUI (see `third_party/open-webui/`). They do not use the course tool budget; each change is sent to the page and the final list is saved with the answer. |
| `evidence.py` | Evidence IDs (`E1`, `E2`, ...) and their sources. Only pages that were read count as evidence. Search results are leads. |
| `result.py` | The run result, the citation patterns, the stop exceptions, the usage entry of a model call (`call_usage`) and the final check of the cited documents (`cited_sources`). |
| `model.py` | The model client: one OpenAI-compatible chat call with native tools, and the sampling settings. |

Search and Direct reuse the evidence and citation code of the research agent. So all three modes are stored, shown and audited in the same way.

## Where it is called

`routers/chat.py` checks the lab and the membership, picks the mode, and calls `run_persisted_research` in `services/research_service.py`. That service saves the answer with its citations, bills each model call and sends the server-sent events.

## Models

The agent uses the main model, the same one as `view_image` and the language call. The model slots are resolved in `services/model_routing.py`, and the HTTP clients are shared per endpoint (`services/shared_clients.py`). The search tool uses the embeddings and the reranker through `services/retrieval_service.py`.

## Tests

Tests are in `backend/tests/` (for example `test_m4_*`, `test_search_mode.py`, `test_course_tools.py`, `test_history_trim.py`). Run them with `.venv/bin/python -m pytest backend/tests -q`. The language call is off in the tests unless a test turns it on (`conftest.py`), so the model replies a test scripts come in the expected order.
