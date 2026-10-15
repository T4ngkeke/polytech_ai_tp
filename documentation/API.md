> Part of the Edu-LLM documentation. Back to [README](../README.md).

## 4. RBAC Authorization (JWT + FastAPI Depends)

> **Stale Token Prevention:**
> The JWT payload MUST ONLY contain `user_id`. Do NOT store `role` or `quota` in the JWT.
> The `Depends` function must query the `Users` table in real-time to fetch the current role and status.

| Depends Function | Who Gets In | Rejection |
| --- | --- | --- |
| `get_current_user` | Any valid JWT whose user is not deleted | 401 |
| `require_teacher` | role == `teacher` OR `admin` | 403 |
| `require_admin` | role == `admin` ONLY | 403 |

---

## 5. API Routing Contract

> **Rule:** Every router calls its own service layer. No router ever calls another router's functions.
> Admin routes bypass ownership checks; Teacher routes enforce ownership.

### A. Auth & Admin Operations

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/auth/signup` | — | Student self-registration (Default role: student). Fails if username exists. |
| POST | `/api/auth/login` | — | Validate credentials. Return JWT containing ONLY `user_id`. |
| POST | `/api/auth/change-password` | get_current_user | Self-service password change. Requires `old_password` (verified) + `new_password`; re-hashes (bcrypt). Any role. |
| GET | `/api/admin/users` | require_admin | List all non-deleted users. |
| PUT | `/api/admin/users/{id}/password` | require_admin | Admin reset of any user's password (no old password required). |
| POST | `/api/admin/users` | require_admin | Create a single new user. |
| PUT | `/api/admin/users/{id}/role` | require_admin | Change user role (e.g., student ↔ teacher). |
| PUT | `/api/admin/users/{id}/quota` | require_admin | Update user's daily token quota. |
| DELETE | `/api/admin/users/{id}` | require_admin | Soft-delete a user. |
| POST | `/api/admin/users/import` | require_admin | CSV Bulk Import. Dry-run by default. `?force=true` executes. |
| GET | `/api/admin/llm/config` | require_admin | Read the current model-routing config: the main LLM, plus the embedding / rerank / ingest / agent / vision slots, the cost weights and the budgets. |
| PUT | `/api/admin/llm/config` | require_admin | Zero-downtime update of SystemConfigs (all keys above). An empty slot falls back to the main LLM — except the vision slot, where empty means the feature is off. |
| GET | `/api/admin/classes` | require_admin | God Mode: List all classes across the system (no ownership filter). |
| GET | `/api/admin/classes/{class_id}/labs` | require_admin | God Mode: List all labs in a class. |
| GET | `/api/admin/classes/{class_id}/students` | require_admin | God Mode: View all students in a specific class. |
| GET | `/api/admin/labs` | require_admin | God Mode: List all labs across the system. |
| PUT | `/api/admin/classes/{class_id}/transfer` | require_admin | Force transfer class ownership (`teacher_id`). |
| DELETE | `/api/admin/classes/{class_id}` | require_admin | Force soft-delete any class (bypasses ownership). |
| PUT | `/api/admin/labs/{lab_id}` | require_admin | God-mode lab update: lock/unlock (`is_active`) or rename any lab (bypasses ownership). The teacher route still enforces ownership. |
| DELETE | `/api/admin/labs/{lab_id}` | require_admin | Force soft-delete any lab (bypasses ownership). |
| GET | `/api/admin/analytics` | require_admin | Global platform-wide token usage dashboard (hierarchical). |
| GET | `/api/admin/analytics/classes/{class_id}` | require_admin | Per-class hierarchical analytics (class→lab→student). |
| DELETE | `/api/admin/sessions/{session_id}` | require_admin | Hard-delete a specific session to purge inappropriate content. |
| POST | `/api/admin/maintenance/prune` | require_admin | Bulk hard-delete sessions/messages older than `older_than_days`. With `dry_run: true`, only counts what would be deleted (shown before confirming). |

### B. Teacher Audit & Control

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | require_teacher | Create class and generate 6-char `invite_code`. |
| GET | `/api/teacher/classes` | require_teacher | List own classes (name, invite code, student count). |
| PUT | `/api/teacher/classes/{class_id}` | require_teacher | Rename/update class details (ownership enforced). |
| DELETE | `/api/teacher/classes/{class_id}` | require_teacher | Soft-delete own class. |
| POST | `/api/teacher/classes/{class_id}/reset-code` | require_teacher | Generate new invite code (invalidating the old one). |
| PUT | `/api/teacher/classes/{class_id}/exam-mode` | require_teacher | Body `{exam_mode: bool}`. On: every lab of the class answers in Direct mode only (no course search), enforced by `POST /api/chat/stream`. Returns the class. |
| GET | `/api/teacher/students` | require_teacher | List every student across the teacher's own classes. |
| GET | `/api/teacher/classes/{class_id}/students` | require_teacher | List the students in one owned class. |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}` | require_teacher | Kick/Remove a student from the class. |
| POST | `/api/teacher/classes/{class_id}/students/{student_id}/quota-refill` | require_teacher | Give a student of the class a full daily quota again for today. Usage is kept: the teacher grants what the student used today beyond earlier grants (`quota_grants`); nothing when a full quota is still left. Returns `{used, limit, granted}`; `404` when the student is not in the class. |
| POST | `/api/teacher/classes/{class_id}/labs` | require_teacher | Create a Lab under a Class (ownership enforced). |
| GET | `/api/teacher/classes/{class_id}/labs` | require_teacher | List labs for a class. |
| PUT | `/api/teacher/labs/{lab_id}` | require_teacher | Rename lab or toggle `is_active` (Open/Close). |
| DELETE | `/api/teacher/labs/{lab_id}` | require_teacher | Soft-delete a Lab (ownership enforced). |
| GET | `/api/teacher/rules` | require_teacher | Get existing rules. Filters: `?level=X&target_id=Y` |
| PUT | `/api/teacher/rules` | require_teacher | Create/Update Rules (Class / Lab / Student level; `skill.md` = class level). |
| GET | `/api/teacher/skill-presets` | require_teacher | List the teacher's own skill presets (`id / name / content`). |
| POST | `/api/teacher/skill-presets` | require_teacher | Create a skill preset (`name`, `content`). |
| PUT | `/api/teacher/skill-presets/{preset_id}` | require_teacher | Update an owned preset (ownership enforced). |
| DELETE | `/api/teacher/skill-presets/{preset_id}` | require_teacher | Delete an owned preset (does not affect classes that already snapshot-copied it). |
| POST | `/api/teacher/classes/{class_id}/skill` | require_teacher | Apply a preset to a class — **snapshot-copies** `preset.content` into the class's `level=class` skill rule. Body: `{preset_id}` or raw `{content}` for an ad-hoc edit. |
| GET | `/api/teacher/chat-history` | require_teacher | Fetch chat history. Filters: `?class_id=X&lab_id=Y&student_id=Z&session_id=W`. **Includes student soft-deleted sessions** (each row carries `is_deleted` so the UI can flag the hidden ones). |
| GET | `/api/teacher/labs/{lab_id}/citation-report` | require_teacher (lab owner) | Which course files, pages and passages the lab's research answers cite, with the students and their questions; the lab's published files no answer cited; questions without a verified answer (`partial_*` or failed runs); answers a student marked down. `?start=YYYY-MM-DD&end=YYYY-MM-DD` (days included, UTC; 422 if end is before start). Test drives are left out; sessions a student deleted are kept. Counts are distinct answers (assistant messages), students and passages. Shape: `backend/app/services/citation_report.py`. |
| GET | `/api/teacher/analytics/classes/{class_id}` | require_teacher | Hierarchical token usage: class → lab → student breakdown. |
| GET | `/api/teacher/analytics/classes/{class_id}/details` | require_teacher | The Analytics tab, for the class or one lab (`?lab_id=`): overview (members, active in 7 days, questions, conversations, thumbs, students at their limit today), answers by mode, questions per day and per weekday × hour over 30 days (Paris time), every student (questions, conversations, last active, today's usage and limit), the latest 20 thumbs-down questions and the latest 20 answers that failed or stopped early. Teacher test sessions are left out. |
| POST | `/api/teacher/labs/{lab_id}/reports` | require_teacher | Start an AI report on the lab, or on one student of it (`{"student_id"}`). `202`: the report is written in the background (about 30 s); poll the GET. While one is running for the same lab and student, it is returned instead of a new one. `404` when the student has no conversation in the lab; `503 agent_model_not_configured`. |
| GET | `/api/teacher/labs/{lab_id}/reports` | require_teacher | The latest report on the lab, or on one student (`?student_id=`): `{"report": {id, status (running, done, failed), error (no_questions, interrupted, ...), content (Markdown), facts (the counted numbers it is based on), model, created_at, finished_at}}` or `{"report": null}`. A report still running after 10 minutes is shown as failed (`interrupted`). |
| POST | `/api/teacher/labs/{lab_id}/documents` | require_teacher | Upload a course **PDF** for this lab only; it is read by MinerU and indexed, and published at once when every page was read (otherwise it stays a draft the teacher publishes). Multipart form; the file is the only field that matters (the language is found from the text when it is indexed); `shared` (older clients) makes it class-wide, as the class upload does. Other formats: `400 only_pdf` (ODP: `odp_not_supported`); structured ingestion off: `409 structured_ingest_disabled`. Replace an existing document with `mode=replace`, `replace_document_id`, `expected_generation`. |
| GET | `/api/teacher/labs/{lab_id}/documents` | require_teacher | The documents the lab uses + status + summary + `ingest_report`: the class-wide documents first, then the lab's own. Each row has `scope`: `class` or `lab`. |
| POST | `/api/teacher/classes/{class_id}/documents` | require_teacher | Upload a class-wide document (`lab_id=NULL`), read by every lab of the class (e.g. the course handout). Same form as the lab upload, without `shared`. |
| GET | `/api/teacher/classes/{class_id}/documents` | require_teacher | The class-wide documents of a class (`scope` = `class`). |
| POST | `/api/teacher/labs/{lab_id}/test-session` | require_teacher | Open a **test-drive** session (`is_test`) on an owned lab — chat as a preview (membership bypassed). |
| DELETE | `/api/teacher/documents/{document_id}` | require_teacher | Delete a document, its chunks and its ingestion jobs, plus the stored file. |

### C. Student Flow (Session & Chat)

> **Audit Integrity Rule:** A student "delete" is a **soft delete** — it sets `is_deleted=True`,
> hiding the session from the student's own views, but the session is permanently retained and
> stays fully visible to teacher/admin audit (flagged as deleted). Students can never purge data;
> only admin can hard-delete a session.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/classes/join` | get_current_user | Submit 6-char `invite_code` to instantly join a Class. |
| GET | `/api/student/classes` | get_current_user | List all joined classes (with labs). Each class has `exam_mode`. |
| GET | `/api/student/classes/{class_id}/labs` | get_current_user | List active labs for a joined class. |
| DELETE | `/api/student/classes/{class_id}/leave` | get_current_user | Voluntarily leave a joined class. |
| POST | `/api/student/labs/{lab_id}/sessions` | get_current_user | Create a new Chat Session specifically within a Lab. `title` is optional: the app sends none and the first answer gives the title (`title` event). |
| GET | `/api/student/sessions` | get_current_user | List own sessions (optional `?lab_id=` filter). |
| GET | `/api/student/sessions/{session_id}` | get_current_user | Fetch message history. **IDOR check required.** |
| PUT | `/api/student/sessions/{session_id}` | get_current_user | Rename a chat session title (cosmetic only). |
| DELETE | `/api/student/sessions/{session_id}` | get_current_user | **Soft-delete (hide)** own session: sets `is_deleted=True`. Hidden from the student; retained and teacher/admin-visible. **IDOR check required.** |
| GET | `/api/student/usage` | get_current_user | Today's token usage vs. today's limit (daily quota plus teacher grants). |
| POST | `/api/chat/stream` | get_current_user | Core chat endpoint. Scoped by `session_id`. SSE streaming. The research agent answers (see below); it needs `AGENTIC_SEARCH_ENABLED` (else `503 chat_not_enabled`) and a session in a lab (else `400 session_without_lab`). |
| POST | `/api/chat/messages/{message_id}/feedback` | get_current_user | 👍/👎 on an assistant message (body `{feedback: up\|down}`). A student may only rate a message in a session they own. |

#### Research agent (`AGENTIC_SEARCH_ENABLED`)

For a session in a lab, `POST /api/chat/stream` answers in one of three
modes over the published PDF revisions of the lab and its class. The answer
is saved, with its citations, before any of it is sent.

Request body, besides `session_id` and `message`:

- `client_request_id` (UUID, optional): the same ID returns the saved run
  again, without a second model call or charge.
- `mode` (optional, default `research`):
  - `research`: the research agent (`backend/app/agent/runner.py`), several
    searches and reads, may ask the student back;
  - `search`: one search, the found pages and their neighbours, one answer
    (`backend/app/agent/search.py`); same citations;
  - `direct`: one answer without the course files and without citations
    (`backend/app/agent/direct.py`).

  A class in exam mode answers in `direct` mode whatever is sent. The
  teacher's rules apply in every mode.

Server-sent events, in order:

| Event | Data |
| --- | --- |
| `status` | `{"route": "agent", "phase": ..., "mode": ..., "exam_mode": ...}`; phase is `thinking`, then `finding` (search), `reading` (read, open, grep), `checking_image` (view_image) or `checking_sources` (answer). The first status names the mode the server applies and whether the class is in exam mode. |
| `draft` | Reply text as the model writes it, as a JSON string; append it. Its sources are not checked yet; show it as a draft. |
| `draft_reset` | `{}`: the draft so far is withdrawn (the model went on researching). |
| (default) | The whole checked answer text, as one JSON string. It replaces the draft. |
| `clarification` | Only when the answer is a question back to the student (the `ask_student` tool): `{"question": ..., "options": [2 to 4 short answers]}`. The client shows the options as buttons; the picked option, or any typed answer, is the next message. The next run of the session cannot ask again. |
| `citations` | The cited sources: `citation_id`, `label` (E1, ...), `document_id`, `revision_id`, `page_id`, `pdf_page_no`, `block_id`, `range`, `origin`, `available`, `href`, `filename`, `newer_revision`, `teacher_revised`, `asset_id`, `asset_mime`, `box` (the cited block's region as `[x0, y0, x1, y1]` fractions of the page, or `null`). |
| `done` | `{"message_id": ...}` |
| `title` | Only after the first answer of a conversation without a title: `{"session_id": ..., "title": ...}`. The answer model writes it from the first question and answer (at most 6 words, in the student's language; the first line of the question if the model fails). A title the student wrote is kept; teacher test sessions get none. Not charged to the quota. |
| `error` | `{"detail": ...}`, for example when another research request of the session is still running, or `Daily token quota exceeded` when the quota runs out during research. |
| (comment) | `: keep-alive`, every 15 s without another event, so proxies keep the stream open. SSE clients ignore it. |

`GET /api/student/sessions/{session_id}` returns the same citations on each
message, with `available` and `href` rechecked against current access,
`clarification` on a question back, and `answer_mode` (`direct`, `search`,
`research`; `null` for answers written before the modes).

The chat shows a cited page with its passages highlighted from
`GET /api/documents/{document_id}/revisions/{revision_id}/pages/{page_no}/image?session_id=`:
one page of the revision's PDF as a PNG (at most 1400 px), with the same
access checks as the revision's PDF; a page the revision does not have is 404.

Models (system configuration):

- `AGENT_BASE_URL`, `AGENT_API_KEY`, `AGENT_MODEL`: the research model
  (default: the main LLM). Its server must support native tool calls (for
  vLLM: `--enable-auto-tool-choice` and a `--tool-call-parser`).
- `VLM_BASE_URL`, `VLM_API_KEY`, `VLM_MODEL`: the vision model behind the
  `view_image` tool. Without `VLM_MODEL`, `view_image` returns
  `model_unavailable`. The system is meant to run one model, so these usually
  name the same model as `AGENT_*`.

A failed `view_image` call tells the model why: `vision_timeout` and
`vision_bad_output` (with the hint to ask a narrower question),
`vision_unreachable`, or `image_unavailable` (the image file could not be read
or rendered). A failed call takes a tool slot but not a visual call. Long
observations and uncertainties are shortened, not refused.

Limits of one research run (system configuration, whole numbers; an empty or
invalid value means the default; the admin API rejects values out of range):

| Key (`/api/admin/llm/config` field) | Default | Range | Meaning |
| --- | --- | --- | --- |
| `AGENT_MAX_TOOL_CALLS` (`agent_max_tool_calls`) | 20 | 1–40 | Course tool calls. |
| `AGENT_MAX_VISUAL_CALLS` (`agent_max_visual_calls`) | 4 | 0–10 | `view_image` calls. |
| `AGENT_RESEARCH_SECONDS` (`agent_research_seconds`) | 300 | 5–1800 | Time for research; then the tools are withdrawn. At most the total time. |
| `AGENT_TOTAL_SECONDS` (`agent_total_seconds`) | 420 | 10–1800 | Time for the whole run, answer included. |
| `AGENT_CALL_SECONDS` (`agent_call_seconds`) | 120 | 5–600 | Time for one model call, `view_image` included. A `view_image` call also ends with the research time. |
| `AGENT_MAX_INPUT_TOKENS` (`agent_max_input_tokens`) | 28000 | 4000–200000 | Estimated input tokens of one model call. Chat history may use a quarter of it. |

The system prompt states the tool and visual limits. The defaults suit
Qwen3.8-27B and Qwen3.8-Flash-Next on one DGX Spark (2026-10-12 end-to-end
test): a long answer can take two minutes, so the time between research and
total should be at least one model call. The 2026-10 benchmark's "standard"
budget was 8, 2, 45, 60, 20 and 12000. `GET /api/admin/llm/config` also
returns `research_limits`: the default, minimum and maximum of each field.
It also returns, read-only, `features` (`research_agent`,
`structured_documents`, `pdf_documents`: the deployment flags) and
`mineru_default_url` (the backend's `PDF_MINERU_SERVER_URL`). The field
`mineru_server_url` (SystemConfig `PDF_MINERU_SERVER_URL`) switches the PDF
worker to another MinerU server at its next tick; empty uses the worker's own
`PDF_MINERU_SERVER_URL`.

Each model call is reserved on the student's daily quota before it starts
and settled with the reported usage afterwards.

