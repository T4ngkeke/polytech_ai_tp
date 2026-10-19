> Part of the Edu-LLM documentation. Back to [README](../README.md).

# API reference

This file lists every HTTP endpoint of the backend, who may call it and what it does. The tables follow the order of the areas: Auth, Student, Chat, Teacher, Admin, Monitoring, Documents, Health. The tables in [DATABASE.md](DATABASE.md) describe the data behind them. The request and response fields are defined in `backend/app/schemas.py`, and the interactive list is at `/docs` on the backend.

## Access rules

This section says how a request is checked. The login token holds only the user id. The role and the quota are read from the `users` table on every request, so a changed role or a deleted user takes effect at once.

| Auth column | Check in code | Who gets in | Otherwise |
| --- | --- | --- | --- |
| none | none | anyone | |
| Any user | `get_current_user` | any valid token whose user is not deleted | 401 |
| Student | `require_student` | role `student` only | 403 |
| Teacher | `require_teacher` | role `teacher` or `admin` | 403 |
| Admin | `require_admin` | role `admin` only | 403 |

A teacher can only act on their own classes, labs and documents (another teacher's class gets `403`, a missing one `404`). Admin endpoints have no ownership check. Teacher endpoints accept an admin token, but an admin owns no class, so in practice they return nothing for an admin.

## Auth

This area covers sign-up, login and the user's own profile and password.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/auth/signup` | none | Student self-registration. The role is always `student`. Fails if the username exists. Returns a token. |
| POST | `/api/auth/login` | none | Checks the credentials. Returns a token that holds only `user_id`. A deleted user cannot log in. |
| GET | `/api/auth/me` | Any user | The signed-in user: id, username, role, daily quota and `is_deleted`. The app calls it after login. |
| POST | `/api/auth/change-password` | Any user | Changes the user's own password. Needs `old_password` (checked) and `new_password`. |

## Student

This area covers joining classes, labs and chat sessions, and the daily usage. A student's "delete" of a session is a soft delete: the session is hidden from the student, and teachers and admins still see it, marked as deleted. Only an admin can delete a session for good.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/classes/join` | Student | Joins a class with its 6-character `invite_code`. Teachers and admins get `403`, so their questions never count in a class's analytics. |
| GET | `/api/student/classes` | Any user | The joined classes, with their labs. Each class has `exam_mode`. |
| GET | `/api/student/classes/{class_id}/labs` | Any user | The active labs of a joined class. |
| DELETE | `/api/student/classes/{class_id}/leave` | Any user | Leaves a joined class. |
| POST | `/api/student/labs/{lab_id}/sessions` | Any user | Creates a chat session in a lab. `title` is optional. The app sends none, and the first answer gives the title (the `title` event). |
| GET | `/api/student/sessions` | Any user | The user's own sessions. Optional filter `?lab_id=`. |
| GET | `/api/student/sessions/{session_id}` | Any user | The messages of one own session. Each answer has its citations (with `available` and `href` checked again against current access), `clarification` for a question back, and `answer_mode` (`direct`, `search`, `research`; `null` for answers written before the modes). |
| PUT | `/api/student/sessions/{session_id}` | Any user | Renames an own session. |
| DELETE | `/api/student/sessions/{session_id}` | Any user | Soft-deletes an own session (see above). |
| GET | `/api/student/usage` | Any user | Today's usage and today's limit. The limit is the daily quota plus the grants of the day. |

## Chat

This area covers the chat itself and the thumbs up or down on an answer.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/chat/stream` | Any user | Answers one message of an own session, as server-sent events (see below). Needs `AGENTIC_SEARCH_ENABLED` (else `503 chat_not_enabled`) and a session in a lab (else `400 session_without_lab`). A locked or deleted lab, or a lab of a class the user has not joined, gets `403`. A used-up quota gets `429`. |
| POST | `/api/chat/messages/{message_id}/feedback` | Any user | Thumbs up or down on an answer. Body `{feedback: "up" \| "down"}`. A student can only rate a message of a session they own. |

### The chat stream

The request body has `session_id` and `message`, and two optional fields:

- `client_request_id` (UUID): the same ID returns the saved run again, with no second model call and no second charge.
- `mode` (default `research`):
  - `research`: the research agent (`backend/app/agent/runner.py`) searches and reads several times, and may ask the student a question back.
  - `search`: one search, the found pages and their neighbours, and one answer (`backend/app/agent/search.py`). It has the same citations.
  - `direct`: one answer without the course files and without citations (`backend/app/agent/direct.py`).

A class in exam mode answers in `direct` mode whatever is sent. The teacher's rules apply in every mode. The answer is saved, with its citations, before any of it is sent. How the answer is made is described in [ARCHITECTURE.md](ARCHITECTURE.md).

The events, in order:

| Event | Data |
| --- | --- |
| `status` | `{"route": "agent", "phase": ..., "mode": ..., "exam_mode": ...}`. The phase is `thinking`, then `finding` (search), `reading` (read, open, grep), `checking_image` (view_image) or `checking_sources` (answer). The first status names the mode the server applies and whether the class is in exam mode. |
| `draft` | Reply text as the model writes it, as a JSON string. Append it. Its sources are not checked yet, so show it as a draft. |
| `draft_reset` | `{}`. The draft so far is withdrawn (the model went on researching). |
| (default) | The whole checked answer, as one JSON string. It replaces the draft. |
| `clarification` | Only when the answer is a question back to the student: `{"question": ..., "options": [2 to 4 short answers]}`. The client shows the options as buttons. The picked option, or any typed answer, is the next message. The next run of the session cannot ask again. |
| `citations` | The cited sources: `citation_id`, `label` (E1, ...), `document_id`, `revision_id`, `page_id`, `pdf_page_no`, `block_id`, `range`, `origin`, `available`, `href`, `filename`, `newer_revision`, `teacher_revised`, `asset_id`, `asset_mime`, and `box` (the region of the cited block as `[x0, y0, x1, y1]` fractions of the page, or `null`). |
| `done` | `{"message_id": ...}` |
| `title` | Only after the first answer of a conversation that has no title: `{"session_id": ..., "title": ...}`. The answer model writes it from the first question and answer (at most 6 words, in the student's language; the first line of the question if the model fails). A title the student wrote is kept. Teacher test sessions get none. It is not charged to the quota. |
| `error` | `{"detail": ...}`. For example when another research request of the session is still running, or `Daily token quota exceeded` when the quota runs out during research. |
| (comment) | `: keep-alive`, every 15 s without another event, so proxies keep the stream open. Clients ignore it. |

Each model call is reserved on the student's daily quota before it starts, and settled with the reported usage afterwards.

### Settings of the research agent

The research agent reads its model and its limits from `system_configs` (see [DATABASE.md](DATABASE.md)). The admin changes them with `PUT /api/admin/llm/config`.

- `AGENT_BASE_URL`, `AGENT_API_KEY`, `AGENT_MODEL`: the model of the agent (empty means the main model). Its server must support native tool calls (for vLLM: `--enable-auto-tool-choice` and a `--tool-call-parser`).
- `VLM_BASE_URL`, `VLM_API_KEY`, `VLM_MODEL`: the vision model behind the `view_image` tool. Without `VLM_MODEL`, `view_image` returns `model_unavailable`. A failed `view_image` call tells the model why: `vision_timeout`, `vision_bad_output` (with the hint to ask a narrower question), `vision_unreachable` or `image_unavailable`. A failed call uses a tool slot but not a visual call.

Limits of one research run. Values are whole numbers. An empty or invalid value means the default, and the admin API rejects a value out of range.

| Key (field in `/api/admin/llm/config`) | Default | Range | Meaning |
| --- | --- | --- | --- |
| `AGENT_MAX_TOOL_CALLS` (`agent_max_tool_calls`) | 30 | 1-40 | Course tool calls. |
| `AGENT_MAX_VISUAL_CALLS` (`agent_max_visual_calls`) | 4 | 0-10 | `view_image` calls. |
| `AGENT_RESEARCH_SECONDS` (`agent_research_seconds`) | 300 | 5-1800 | Time for research. After it the tools are withdrawn. At most the total time. |
| `AGENT_TOTAL_SECONDS` (`agent_total_seconds`) | 420 | 10-1800 | Time for the whole run, answer included. |
| `AGENT_CALL_SECONDS` (`agent_call_seconds`) | 120 | 5-600 | Time for one model call, `view_image` included. |
| `AGENT_MAX_INPUT_TOKENS` (`agent_max_input_tokens`) | 28000 | 4000-200000 | Estimated input tokens of one model call. The chat history may use a quarter of it. |

The system prompt states the tool and image limits. A long answer can take two minutes, so the total time should be at least one model call longer than the research time.

## Teacher

This area covers what a teacher does with their classes and labs: classes and students, labs, rules, chat history, analytics and reports, and uploading and listing documents. The endpoints that read, edit and publish a document version are in [Documents](#documents).

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | Teacher | Creates a class with a 6-character `invite_code`. The class starts in exam mode when `NEW_CLASS_EXAM_MODE` is on (the default). |
| GET | `/api/teacher/classes` | Teacher | The teacher's own classes: name, invite code, student count, exam mode. |
| PUT | `/api/teacher/classes/{class_id}` | Teacher | Renames a class. |
| DELETE | `/api/teacher/classes/{class_id}` | Teacher | Soft-deletes an own class. |
| POST | `/api/teacher/classes/{class_id}/reset-code` | Teacher | Makes a new invite code. The old one stops working. |
| PUT | `/api/teacher/classes/{class_id}/exam-mode` | Teacher | Body `{exam_mode: bool}`. On: every lab of the class answers in Direct mode only, enforced by `POST /api/chat/stream`. Returns the class. |
| GET | `/api/teacher/students` | Teacher | Every student of the teacher's classes. |
| GET | `/api/teacher/classes/{class_id}/students` | Teacher | The students of one own class. |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}` | Teacher | Removes a student from the class. |
| POST | `/api/teacher/classes/{class_id}/students/{student_id}/quota-refill` | Teacher | Gives the student a full daily quota again for today. Usage is kept: the teacher grants what the student used today beyond earlier grants (rows in `quota_grants`), and nothing when a full quota is still left. Returns `{used, limit, granted}`. `404` when the student is not in the class. |
| POST | `/api/teacher/classes/{class_id}/labs` | Teacher | Creates a lab in an own class. |
| GET | `/api/teacher/classes/{class_id}/labs` | Teacher | The labs of an own class. |
| PUT | `/api/teacher/labs/{lab_id}` | Teacher | Renames a lab, or opens or locks it with `is_active`. A locked lab refuses chat. |
| DELETE | `/api/teacher/labs/{lab_id}` | Teacher | Soft-deletes an own lab. |
| POST | `/api/teacher/labs/{lab_id}/test-session` | Teacher | Opens a test-drive session (`is_test`) in an own lab. The teacher chats as a preview, without being a member. The session is left out of analytics and of the citation report. |
| GET | `/api/teacher/rules` | Teacher | Reads rules. Filters `?level=` and `?target_id=`. |
| PUT | `/api/teacher/rules` | Teacher | Creates or updates a rule at level `class`, `lab` or `student`. The class-level rule is the `skill.md` text that sets the tutor's style. |
| GET | `/api/teacher/skill-presets` | Teacher | The teacher's own skill presets (`id`, `name`, `content`). |
| POST | `/api/teacher/skill-presets` | Teacher | Creates a preset (`name`, `content`). |
| PUT | `/api/teacher/skill-presets/{preset_id}` | Teacher | Updates an own preset. |
| DELETE | `/api/teacher/skill-presets/{preset_id}` | Teacher | Deletes an own preset. Classes that already copied it keep their text. |
| POST | `/api/teacher/classes/{class_id}/skill` | Teacher | Applies a preset to a class by copying its text into the class-level rule. Body `{preset_id}`, or `{content}` for a text written by hand. |
| GET | `/api/teacher/chat-history` | Teacher | Chat history. Filters `?class_id=`, `?lab_id=`, `?student_id=`, `?session_id=`. It includes the sessions that students deleted; each row has `is_deleted`. |
| GET | `/api/teacher/analytics/classes/{class_id}` | Teacher | Token use of a class: class, then lab, then student. |
| GET | `/api/teacher/analytics/classes/{class_id}/details` | Teacher | The Analytics tab, for the class or one lab (`?lab_id=`). It has an overview (members, active in 7 days, questions, conversations, thumbs, students at their limit today), answers by mode, questions per day and per weekday and hour over 30 days (Paris time), every student (questions, conversations, last active, today's usage and limit), the latest 20 thumbs-down questions and the latest 20 answers that failed or stopped early. Teacher test sessions are left out. |
| GET | `/api/teacher/labs/{lab_id}/citation-report` | Teacher | Which course files, pages and passages the research answers of the lab cite, with the students and their questions. It also lists the published files that no answer cited, the questions without a verified answer, and the answers a student marked down. `?start=YYYY-MM-DD&end=YYYY-MM-DD` (days included, UTC; `422` if the end is before the start). Test drives are left out. Sessions that students deleted are kept. The shape is in `backend/app/services/citation_report.py`. |
| POST | `/api/teacher/labs/{lab_id}/reports` | Teacher | Starts an AI report on the lab, or on one student of it (`{"student_id"}`). Returns `202` and writes the report in the background (about 30 s). While a report of the same lab and student is running, that one is returned. `404` when the student has no conversation in the lab. `503 agent_model_not_configured`. |
| GET | `/api/teacher/labs/{lab_id}/reports` | Teacher | The latest report on the lab, or on one student (`?student_id=`): `{"report": {id, status, error, content, facts, model, created_at, finished_at}}`, or `{"report": null}`. `status` is `running`, `done` or `failed`. `content` is Markdown. `facts` holds the counted numbers the report is based on. A report that still runs after 10 minutes is shown as failed (`interrupted`). |
| POST | `/api/teacher/labs/{lab_id}/documents` | Teacher | Uploads a course PDF for this lab. Multipart form. The file is the only field that matters (the language is found from the text). `shared` (older clients) makes the file class-wide. A new file is read by MinerU and indexed, then published at once when every page was read. Otherwise it stays a draft that the teacher publishes. To replace a document, send `mode=replace`, `replace_document_id` and `expected_generation`. Errors: `400 only_pdf` for another format (`odp_not_supported` for ODP), `413 file_too_large` (limit 200 MB), `400 invalid_pdf` or `pdf_password_or_page_limit` (at most 150 pages, no password), `409 structured_ingest_disabled` when the ingestion flags are off. Sending the same file again to the same document returns `200` and creates nothing. |
| POST | `/api/teacher/classes/{class_id}/documents` | Teacher | Uploads a class-wide PDF, which every lab of the class reads (for example the course handout). Same form and errors as the lab upload, without `shared`. |
| GET | `/api/teacher/labs/{lab_id}/documents` | Teacher | The documents a lab uses, with status, summary and `ingest_report`: the class-wide documents first, then the lab's own. Each row has `scope` (`class` or `lab`). While a PDF waits or is read, `latest_revision` also has `jobs_ahead` (reading jobs that the worker takes first) or `progress` (`{done, total, seconds_left}`: pages read; `seconds_left` is `null` before 3 pages). |
| GET | `/api/teacher/classes/{class_id}/documents` | Teacher | The class-wide documents of a class. Same fields as above, with `scope` = `class`. |
| DELETE | `/api/teacher/documents/{document_id}` | Teacher | Deletes a document. For a structured document the query `expected_generation` is required (`422 expected_generation_required`), and the answer is `202 {"document_id", "state": "pending"}`: the stored files and chunks are removed in the background. |

## Admin

This area covers users, classes, the model settings, data pruning and global analytics. Admin endpoints have no ownership check.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/admin/users` | Admin | All users that are not deleted. |
| POST | `/api/admin/users` | Admin | Creates one user. |
| POST | `/api/admin/users/import` | Admin | Imports users from a CSV file. A dry run by default. `?force=true` applies it. |
| PUT | `/api/admin/users/{user_id}/role` | Admin | Changes the role (student, teacher, admin). |
| PUT | `/api/admin/users/{user_id}/quota` | Admin | Sets the daily token quota. |
| PUT | `/api/admin/users/{user_id}/password` | Admin | Resets a password. No old password is needed. |
| DELETE | `/api/admin/users/{user_id}` | Admin | Soft-deletes a user. |
| GET | `/api/admin/classes` | Admin | All classes (`?skip=`, `?limit=`, at most 500). Each class has its owner and counts: `teacher_username`, `teacher_ok` (false when the owner was deleted or is no longer a teacher or admin, so nobody can manage the class until it is transferred), `lab_count`, `student_count`, `questions_7d` and `last_active`. Teacher test sessions are not counted. |
| GET | `/api/admin/classes/{class_id}/labs` | Admin | The labs of a class. |
| GET | `/api/admin/classes/{class_id}/students` | Admin | The students of a class. |
| PUT | `/api/admin/classes/{class_id}/transfer` | Admin | Gives the class to another teacher (`teacher_id`). |
| DELETE | `/api/admin/classes/{class_id}` | Admin | Soft-deletes any class. |
| GET | `/api/admin/labs` | Admin | All labs. |
| PUT | `/api/admin/labs/{lab_id}` | Admin | Renames any lab, or opens or locks it with `is_active`. |
| DELETE | `/api/admin/labs/{lab_id}` | Admin | Soft-deletes any lab. |
| GET | `/api/admin/analytics` | Admin | Platform-wide token use: totals, `active_users_7d` and a daily breakdown over 30 days. |
| GET | `/api/admin/analytics/classes/{class_id}` | Admin | Token use of one class: class, then lab, then student. |
| GET | `/api/admin/analytics/classes/{class_id}/details` | Admin | The same data as the teacher's `/details` endpoint, for any class (`?lab_id=` for one lab). No ownership check. `404` if the lab is not in the class. |
| GET | `/api/admin/llm/config` | Admin | The model settings: the main model, and the embedding, rerank, agent and vision slots, the research limits, `context_max_tokens`, the token weights `token_alpha` and `token_beta`, and `mineru_server_url`. An empty value means the slot uses the main model (the vision slot is off instead). It also returns, read-only, `research_limits` (default, minimum and maximum of each limit), `features` (`research_agent`, `structured_documents`, `pdf_documents`: the deployment flags) and `mineru_default_url` (the `PDF_MINERU_SERVER_URL` of the backend). |
| PUT | `/api/admin/llm/config` | Admin | Changes the model settings without a restart. Only the fields that are sent are written. `base_url`, `api_key` and `model` are required. A research limit out of range gets `422`. `mineru_server_url` switches the PDF worker to another MinerU server at its next tick (empty uses the worker's own `PDF_MINERU_SERVER_URL`). |
| GET | `/api/admin/llm/servers` | Admin | What each model server serves. One row per slot (main, agent, vision, embeddings, reranker): `slot`, `label`, `model`, `server`, `status` (`ok`, `model_missing`, `unreachable` or `off`), `served` (model names the server lists) and `max_model_len` (the context length of the configured model, when the server reports it). The servers are asked with a 3-second timeout. |
| DELETE | `/api/admin/sessions/{session_id}` | Admin | Deletes a session and its messages for good. |
| POST | `/api/admin/maintenance/prune` | Admin | Deletes sessions and messages older than `older_than_days`. With `dry_run: true` it only counts them. |

## Monitoring

This area covers the access check for Grafana, which the admin Monitoring tab shows under `/grafana/`. How the dashboards run is in [MONITORING.md](MONITORING.md).

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/admin/monitoring/session` | Admin | Sets the cookie `edullm_monitoring` (path `/grafana/`, HttpOnly, SameSite Strict, 8 hours, signed for monitoring only). The Monitoring tab calls it before it shows Grafana. Returns `204`. |
| GET | `/api/admin/monitoring/check` | Monitoring cookie | Called by nginx (`auth_request`) for every `/grafana/` request. Returns `204` when the cookie belongs to a user who is an admin now, and `401` otherwise. |

## Documents

This area covers the version of a document: its revisions, the edits and reviews before publication, and the reading of pages, blocks and files. A revision is one reading of one uploaded file. It starts as a draft, and the teacher or the worker publishes it. Each change names the document `generation` and, when it concerns a revision, the revision the teacher saw. A change never alters that revision: it makes a new draft. A stale generation gets `409`. A document that is not structured (an old one) gets `409 legacy_document`.

Teacher endpoints work on drafts and published revisions of an own document. Student endpoints work only on published or superseded (replaced by a newer one) revisions of a document in the lab or class of the session (`?session_id=` is required), and the access is checked on every call. Files are streamed after the check. No storage path is ever sent to the browser.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/teacher/documents/{document_id}/revisions` | Teacher | The revisions of a document, with `generation`, `lifecycle`, `current_revision_id` and, for each revision, its processing and publication state, `index_ready` and report. |
| POST | `/api/teacher/documents/{document_id}/revisions/{revision_id}/publish` | Teacher | Publishes a ready revision. Body `{expected_generation, acknowledge_issues}`. Returns `current_revision_id` and `generation`. |
| POST | `/api/teacher/documents/{document_id}/archive` | Teacher | Archives the document. Body `{expected_generation}`. |
| POST | `/api/teacher/documents/{document_id}/restore` | Teacher | Brings an archived document back. Body `{expected_generation}`. |
| PUT | `/api/teacher/documents/{document_id}/blocks/{block_id}` | Teacher | Edits one block (`content`, `kind`, `content_format`, `content_description` or `clear_description`). Body also has `expected_revision_id` and `expected_generation`. Returns the new draft revision. `422` when the edit is refused. |
| POST | `/api/teacher/documents/{document_id}/blocks/{block_id}/exclude` | Teacher | Leaves a block out of search, or puts it back (`excluded`, optional `reason`). Returns the new draft revision. |
| POST | `/api/teacher/documents/{document_id}/blocks/{block_id}/reprocess` | Teacher | Reads the whole file again into a new draft (a single block cannot be read apart from its page). The block is recorded as the reason. Returns the new draft revision and a `job_id`. |
| POST | `/api/teacher/documents/{document_id}/revisions/{revision_id}/reprocess` | Teacher | Reads the whole revision again. Body `{expected_generation}`. Returns the new draft revision and a `job_id`. |
| POST | `/api/teacher/documents/{document_id}/blocks/{block_id}/describe` | Teacher | Asks the vision model for one description of a picture, formula or table block ("Write with AI"). Only the picture is sent, cut out of its page. The description is stored as a candidate that the teacher checks. `409 model_unavailable` when `STRUCTURED_VISION_ENABLED` is off or no vision model is set. `422` when the block has no picture or the description is refused. `503` when the model fails. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/reviews` | Teacher | The review items of a revision: a description from the vision model, or a teacher edit that no longer matched after a new reading, waiting for the teacher's check. Optional `?status=`. |
| PUT | `/api/teacher/documents/{document_id}/revisions/{revision_id}/reviews/{review_id}` | Teacher | Settles a review item: `status` is `confirmed` or `ignored`, optional `content`, with `review_revision` and `expected_generation`. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/outline` | Teacher | The outline of a revision. Optional `page_id` and `cursor`. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/pages/{page_id}` | Teacher | One page with its blocks. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/read` | Teacher | Reads text by page, block or character range, within a `budget` (default 8000, at most 20000 characters). Optional `cursor`. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/navigate` | Teacher | Moves from a block (`anchor_block_id`) to the next or previous block or page (`direction` = `next` or `previous`, `unit` = `block` or `page`, `count`). |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/grep` | Teacher | Searches the revision for a text pattern (`pattern`, `case_sensitive`, `cursor`). |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/assets/{asset_id}` | Teacher | A stored image or file of the revision. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/preview` | Teacher | The PDF used to show the revision. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/source` | Teacher | The file the teacher uploaded. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/pages/{page_id}` | Any user | One page with its blocks. Also says whether a newer revision exists (`newer_revision_available`). Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/pages/{page_no}/image` | Any user | One page of the revision's PDF as a PNG (at most 1400 px), for the cited-page view. A page the revision does not have gets `404`. Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/assets/{asset_id}` | Any user | A stored image of the revision. Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/preview` | Any user | The PDF used to show the revision. Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/pdf` | Any user | The complete course PDF, with the session's access checked again. Needs `?session_id=`. Optional `?revision_id=`. |

## Health

This area has one endpoint that tells whether the backend runs.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/health` | none | Returns `{"status": "ok", ...}` when the server is up. Docker health checks use it. |
