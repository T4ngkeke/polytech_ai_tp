# Code from Open WebUI

Some agent tools are ported from Open WebUI (https://github.com/open-webui/open-webui),
version v0.13.4, so that they behave as students know them there. The code is
adapted to this backend (no Open WebUI database, events or configuration).

Each ported file starts with Open WebUI's copyright notice and names its source.
The license is in [LICENSE](LICENSE). Edu-LLM does not use the Open WebUI name or
branding.

| File here | Ported from |
| --- | --- |
| `backend/app/agent/web_tools.py` | `backend/open_webui/tools/builtin.py`: `search_web`, `fetch_url`; `backend/open_webui/retrieval/web/utils.py`: `resolve_hostname`, `_embedded_ipv4`, `_assert_addresses_allowed`, `assert_identity_encoding`, `validate_url`, `_ssrf_safe_new_conn`, `_SSRFSafeAdapter`, `get_ssrf_safe_requests_session`, `SafeWebBaseLoader._document_from_html`. The SearXNG request is written here, against SearXNG's JSON API: Open WebUI's sends its own bot name, which its license protects. |
| `backend/app/agent/memory.py` | `backend/open_webui/utils/memory.py`: `MEMORY_CONTEXT_OPEN`/`CLOSE`, `recall_memories` and `add_memory_context`, `review_memory_after_turn`, `_review_memory`, `_generate_memory_operations` (the review prompt, adapted to learning notes; the system line is our own, Open WebUI's names its product and is protected by its license), `validate_memory_operations`. |
| `backend/app/agent/code_tools.py` | `backend/open_webui/tools/builtin.py`: `execute_code` (Pyodide engine: the event to the browser, its output parsing, the image lines); `backend/open_webui/utils/sanitize.py`: `strip_ansi_codes`, `strip_markdown_code_fences`, `sanitize_code`; `backend/open_webui/config.py`: `CODE_INTERPRETER_PYODIDE_PROMPT` (in `CODE_NOTE`). The browser answers over `POST /api/chat/code/{id}` instead of Open WebUI's WebSocket event call. |
| `backend/app/agent/task_tools.py` | `backend/open_webui/tools/builtin.py`: `TaskItem`, `VALID_TASK_STATUSES`, `_task_summary`, `create_tasks`, `update_task` |
