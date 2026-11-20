# Code from Open WebUI

Some agent tools are ported from Open WebUI (https://github.com/open-webui/open-webui),
version v0.13.4, so that they behave as students know them there. The code is
adapted to this backend (no Open WebUI database, events or configuration).

Each ported file starts with Open WebUI's copyright notice and names its source.
The license is in [LICENSE](LICENSE). Edu-LLM does not use the Open WebUI name or
branding.

| File here | Ported from |
| --- | --- |
| `backend/app/agent/task_tools.py` | `backend/open_webui/tools/builtin.py`: `TaskItem`, `VALID_TASK_STATUSES`, `_task_summary`, `create_tasks`, `update_task` |
