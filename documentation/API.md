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
| GET | `/api/auth/me` | Any user | The signed-in user: id, username, role, daily and weekly quota, `is_deleted`, `tour_seen_at`, `must_change_password`, `ui_language`, `about_me` and `memory_enabled`. The app calls it after login. |
| POST | `/api/auth/tour-seen` | Any user | The guided tour was finished or skipped: it no longer starts by itself at sign-in (the ? button still opens it). Sets `tour_seen_at` once. |
| PUT | `/api/auth/settings` | Any user | The user's own settings: `ui_language` (`zh`, `fr`, `en`, or `null` to follow the browser), `about_me` (up to 500 characters; empty or `null` clears it) and `memory_enabled` (off: no memory is read or written; the stored ones stay until deleted). Only the fields sent change. Returns the user as `/me` does. "About me" is for the agent to send with each question, below the teacher's rules (not used yet). |
| POST | `/api/auth/change-password` | Any user | Changes the user's own password. Needs `old_password` (checked) and `new_password` (6 characters or more). After an admin reset (`must_change_password`) the old password is not asked, and the new one may not be the reset password (`400 choose_another_password`). |

## Student

This area covers joining classes, labs and chat sessions, and the daily usage. A student's "delete" of a session is a soft delete: the session is hidden from the student, and teachers and admins still see it, marked as deleted. Only an admin can delete a session for good.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/classes/join` | Student | Joins a class with its 6-character `invite_code`. Teachers and admins get `403`, so their questions never count in a class's analytics. |
| GET | `/api/student/classes` | Any user | The joined classes, with their labs. Each class has `exam_mode`. |
| GET | `/api/student/classes/{class_id}/labs` | Any user | The active labs of a joined class. In exam mode each lab has `exam_files`: how many files it can still search (0: Direct only). Each lab has `features`: what students may use there now, `{upload, web, code, deep}` as true or false (see the teacher feature routes). |
| DELETE | `/api/student/classes/{class_id}/leave` | Any user | Leaves a joined class. |
| POST | `/api/student/labs/{lab_id}/sessions` | Any user | Creates a chat session in a lab. `title` is optional. The app sends none, and the first answer gives the title (the `title` event). |
| GET | `/api/student/sessions` | Any user | The user's own sessions. Optional filter `?lab_id=`. |
| GET | `/api/student/sessions/{session_id}` | Any user | The messages of one own session. Each answer has its citations (with `available` and `href` checked again against current access), `clarification` for a question back, and `answer_mode` (`direct`, `search`, `research`; `null` for answers written before the modes). |
| PUT | `/api/student/sessions/{session_id}` | Any user | Renames an own session (`title`), pins or unpins it (`pinned`), or both. `422 nothing_to_change` when neither is sent. Sessions have `pinned`. |
| GET | `/api/student/search` | Any user | `?q=` (2 to 100 characters). The user's conversations whose title or messages contain it, case-insensitive, newest match first, one per conversation: `{session_id, title, lab_id, lab_name, class_name, pinned, message_id, role, snippet, matched_at}`. A title-only match has no `message_id`. Deleted conversations and test sessions are left out. Accents count ("recursivite" does not find "récursivité"). |
| GET | `/api/student/memories` | Any user | What the assistant remembers about the user, newest first: `{enabled, items: [{id, class_id, class_name, content, category, source_message_id, created_at, updated_at}]}`. Optional `?class_id=`. Memories are kept per class, one sentence each (learning preferences and progress). The agent will write and read them; none are written yet. |
| DELETE | `/api/student/memories/{memory_id}` | Any user | Deletes one own memory for good. `404 memory_not_found` otherwise. |
| DELETE | `/api/student/memories` | Any user | Deletes all own memories for good, or those of `?class_id=`. Returns `{deleted}`. |
| GET | `/api/student/export` | Any user | All the user's visible conversations as one file to download: `?format=json` (default) or `md`. Deleted conversations and teacher test sessions are left out. Citation markers such as `[E1]` stay in the text. |
| DELETE | `/api/student/sessions/{session_id}` | Any user | Soft-deletes an own session (see above). |
| GET | `/api/student/usage` | Any user | `{used, limit, week_used, week_limit, blocked, resets_at, week_resets_at, reset_requested}`. A day starts at midnight Paris time, a week on Monday. A limit is the quota plus the grants of that day or week. `blocked` is `day`, `week` or `null`: whether a new question is refused now. `reset_state` is today's request: `asked` (open), `reset`, `ignored`, or `null`; `reset_state_at` when it was asked or answered. |
| POST | `/api/student/usage/reset-request` | Any user | Asks the teachers of the user's classes for a reset. One open request at a time (`sent` is false while one is open, and for the rest of the day after a teacher ignored one); after a reset the student can ask again. `409 not_at_limit` when no limit is reached. Returns the usage above plus `sent`. |

## Chat

This area covers the chat itself and the thumbs up or down on an answer.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/chat/stream` | Any user | Answers one message of an own session, as server-sent events (see below). Needs `AGENTIC_SEARCH_ENABLED` (else `503 chat_not_enabled`) and a session in a lab (else `400 session_without_lab`). A locked or deleted lab, or a lab of a class the user has not joined, gets `403`. A message longer than 20,000 characters gets `422`. A reached limit gets `429` with `day_quota_reached` or `week_quota_reached`. Each of these refusals is stored in `chat_refusals` (for monitoring). The limits are checked only here: a question that starts always finishes, even over the limit, and the next one is refused. |
| GET | `/api/chat/sessions/{session_id}/history-start?mode=` | Any user | Where the history of the next question in an own session would start: `{seen_from, hidden}`. `seen_from` is the first message still sent to the model (`null` when none is) and `hidden` the number of earlier messages that no longer are; `hidden` 0 means everything is sent. The chat page draws a line there. Same rule as `/stream`; `mode` (default `search`) matters because Search and Direct share the context with the TP text. |
| POST | `/api/chat/messages/{message_id}/feedback` | Any user | Thumbs up or down on an answer. Body `{feedback: "up" \| "down"}`. A student can only rate a message of a session they own. |

### The chat stream

The request body has `session_id` and `message`, and two optional fields:

- `client_request_id` (UUID): the same ID returns the saved run again, with no second model call and no second charge.
- `attachments` (list, at most 5, default empty): `{kind, id}` items. `file`: an own uploaded file of this lab, not hidden, needs the lab's `upload` feature. `document`: a course file this lab can read now (in exam mode, an allowed one). `session`: an own earlier chat of this lab, not this one, not in exam mode. A refused attachment refuses the question: `404 attachment_unavailable`, `403 feature_off` or `403 exam_mode`. They are saved on the student's message (`attachments: [{kind, id, name}]` in the session's messages); the agent does not read them yet.
- `version_of` (UUID, optional): a new version of the session's latest current question (the student edited it, or asked for a new answer with the same text). That question and its answer get `superseded_at`; the new question has `version_of` set to the first question of the group. The model's history leaves the replaced turn out; the session's messages keep every version (with `version_of` and `superseded_at`), and so does the audit. `404 message_not_found` when it is not a current question of this session, `409 not_latest_question` when a later one exists.
- `tools` (list, default empty): the tools the student turned on for this question, among `web`, `code` and `deep`. Each one must be allowed in the lab (the lab's `features`), else the question is refused with `403 feature_off`. `deep` (deep research) runs the research agent with more room: 10 more tool calls (40 at most), twice the visual calls and the time, and a note asking for a complete answer (`research_service.deep_limits`, `DEEP_NOTE`). `web` (web search) gives the research agent `search_web` and `fetch_url` (ported from Open WebUI, `backend/app/agent/web_tools.py`): results and pages get IDs `W1`, `W2`, cited as `[W#]` and shown apart from the course citations; only public addresses are fetched. It needs `WEB_SEARCH_URL` (a SearXNG instance), else `503 web_search_not_configured`. `code` comes later; for now the server only checks it.
- `mode` (default `research`). The new interface sends none: one entry, where the research agent decides by itself whether to search the course. `search` and `direct` stay for the current pages and for exam mode:
  - `research`: the research agent (`backend/app/agent/runner.py`) searches and reads several times, and may ask the student a question back.
  - `search`: one search, the found pages and their neighbours, and one answer (`backend/app/agent/search.py`). It has the same citations.
  - `direct`: one answer without searching the course files and without citations (`backend/app/agent/direct.py`). It sees the text of the lab's own files (the TP statement) when that text is short enough, never the class course files.

A class in exam mode searches only the files its teacher allowed in the exam (`documents.exam_allowed`); with none for this lab, it answers in `direct` mode whatever is sent. The teacher's rules apply in every mode. The answer is saved, with its citations, before any of it is sent. While the research agent works, a `tasks` event carries its task checklist each time it changes (`[{id, content, status}]`, status `pending`, `in_progress`, `completed` or `cancelled`; the `create_tasks` and `update_task` tools ported from Open WebUI). The final list is saved on the answer (`tasks` in the session's messages). The `done` event carries `message_id`, `user_message_id`, `web_sources` (`[{id, url, title}]` of the `[W#]` the answer cites, or `null`) and `course_use`: how the answer used the course, `cited`, `not_found` (it searched, it cites nothing) or `none` (it did not search); `null` for a question back. How the answer is made is described in [ARCHITECTURE.md](ARCHITECTURE.md).

The events, in order:

| Event | Data |
| --- | --- |
| `status` | `{"route": "agent", "phase": ..., "mode": ..., "exam_mode": ...}`. The phase is `thinking`, then `finding` (search), `reading` (read, open, grep), `checking_image` (view_image) or `checking_sources` (answer). The first status names the mode the server applies and whether the class is in exam mode. |
| `draft` | Reply text as the model writes it, as a JSON string. Append it. Its sources are not checked yet, so show it as a draft. |
| `draft_reset` | `{}`. The draft so far is withdrawn (the model went on researching). |
| (default) | The whole checked answer, as one JSON string. It replaces the draft. |
| `clarification` | Only when the answer is a question back to the student: `{"question": ..., "options": [2 to 4 short answers]}`. The client shows the options as buttons. The picked option, or any typed answer, is the next message. The next run of the session cannot ask again. |
| `citations` | The cited sources: `citation_id`, `label` (E1, ...), `document_id`, `revision_id`, `page_id`, `pdf_page_no`, `block_id`, `range`, `origin`, `available`, `href`, `filename`, `newer_revision`, `teacher_revised`, `asset_id`, `asset_mime`, and `box` (the region of the cited block as `[x0, y0, x1, y1]` fractions of the page, or `null`). |
| `done` | `{"message_id": ..., "user_message_id": ...}`: the saved answer and question |
| `title` | Only after the first answer of a conversation that has no title: `{"session_id": ..., "title": ...}`. The answer model writes it from the first question and answer (at most 6 words, in the student's language; the first line of the question if the model fails). A title the student wrote is kept. Teacher test sessions get none. It is not charged to the quota. |
| `error` | `{"detail": ...}`. For example when another research request of the session is still running. |
| (comment) | `: keep-alive`, every 15 s without another event, so proxies keep the stream open. Clients ignore it. |

Each model call is reserved before it starts and settled with the reported usage afterwards. The reservation never stops a running question.

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
| `AGENT_MAX_INPUT_TOKENS` (`agent_max_input_tokens`) | 28000 | 4000-200000 | Estimated input tokens of one Research model call, on top of the chat history (the total stays 6,000 tokens under the model's context). |
| `TP_CONTEXT_PERCENT` (`tp_context_percent`) | 30 | 0-60 | Share of the model's context (its `max_model_len`) that the TP files' text may use in Search and Direct. Files go in whole, the shortest first; 0 puts no TP text in the prompt. |
| `HISTORY_CONTEXT_PERCENT` (`history_context_percent`) | 30 | 0-50 | Share of the model's context that the chat history may use. With the TP text (Search, Direct), the two together use at most 75%. Past the limit the oldest turns go, several at once. |

The system prompt states the tool and image limits. A long answer can take two minutes, so the total time should be at least one model call longer than the research time.

## Teacher

This area covers what a teacher does with their classes and labs: classes and students, labs, rules, chat history, analytics and reports, and uploading and listing documents. The endpoints that read, edit and publish a document version are in [Documents](#documents).

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | Teacher | Creates a class with a 6-character `invite_code`. The class starts in exam mode when `NEW_CLASS_EXAM_MODE` is on (the default). |
| GET | `/api/teacher/classes` | Teacher | The teacher's own classes: name, invite code, student count, exam mode. |
| PUT | `/api/teacher/classes/{class_id}` | Teacher | Renames a class. |
| DELETE | `/api/teacher/classes/{class_id}` | Teacher, admin | Soft-deletes an own class (an admin: any class). |
| POST | `/api/teacher/classes/{class_id}/reset-code` | Teacher | Makes a new invite code. The old one stops working. |
| PUT | `/api/teacher/classes/{class_id}/exam-mode` | Teacher | Body `{exam_mode: bool}`. On: students search and open only the files allowed in the exam; a lab without one answers in Direct mode only. Enforced by `POST /api/chat/stream` and the document routes. Returns the class. |
| PUT | `/api/teacher/classes/{class_id}/features` | Teacher | Body: some of `{upload, web, code, deep}` as true or false (upload: files and screenshots; web: web search; code: the code interpreter; deep: deep research). The ones left out keep their value; a new class has all four on. Returns the class with `features`. A lab may choose otherwise, and exam mode turns all four off. |
| GET | `/api/teacher/classes/{class_id}/students/{student_id}/memories` | Teacher, admin | What the assistant remembers about a student of this class (this class only): `{enabled, items}` as in the student route. `404` when the student is not in the class. |
| GET | `/api/teacher/labs/{lab_id}/search` | Teacher, admin | `?q=` (2 to 100 characters). The students' questions in the lab that contain it, case-insensitive, newest first: `{total, students, items}`. Each item: `message_id, session_id, student_id, student_name, question, created_at, session_deleted, answer_mode, cited` (whether the answer cites the course). Deleted sessions are included, as in the audit; teacher test sessions are not. Up to 50 items. |
| GET | `/api/teacher/classes/{class_id}/exam-files` | Teacher | The class's files (whole class and each lab): `id, filename, status, lab_id, lab_name, exam_allowed`. |
| PUT | `/api/teacher/classes/{class_id}/exam-files` | Teacher | Body `{document_ids: [...]}`: these files are allowed in the exam, no others (ids outside the class are ignored). Returns the list above. Can be set while exam mode is off. |
| GET | `/api/teacher/classes/{class_id}/students` | Teacher, admin | The students of one own class (an admin: any class), with today's usage. |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}` | Teacher | Removes a student from the class. |
| POST | `/api/teacher/classes/{class_id}/students/{student_id}/quota-refill` | Teacher | Gives the student a full day and a full week again. Usage is kept: the teacher grants what the student used beyond earlier grants (rows in `quota_grants`, kind `day` and `week`), and nothing for a limit that is still full. Answers the student's reset request of today. Returns `{used, limit, granted, week_used, week_limit}`. `404` when the student is not in the class. |
| POST | `/api/teacher/classes/{class_id}/quota-refill-all` | Teacher | The same for every student of the class. Returns `{students, refilled}`. |
| GET | `/api/teacher/quota-requests` | Teacher | Today's open reset requests of students in the teacher's classes, oldest first: user, classes, usage, limits and which limit blocks. |
| POST | `/api/teacher/quota-requests/{student_id}/ignore` | Teacher | Ignores the student's open request: the limits stay and the student cannot ask again today. `409 no_open_request` without one; `404` for a student outside the teacher's classes. |
| POST | `/api/teacher/quota-requests/{student_id}/unignore` | Teacher | Undoes today's ignore: the request is open again. `409 nothing_to_undo`. |
| POST | `/api/teacher/classes/{class_id}/labs` | Teacher | Creates a lab in an own class. |
| GET | `/api/teacher/classes/{class_id}/labs` | Teacher, admin | The labs of an own class (an admin: any class). |
| PUT | `/api/teacher/labs/{lab_id}` | Teacher, admin | Renames a lab, or opens or locks it with `is_active`. A locked lab refuses chat. An admin may change any lab. |
| PUT | `/api/teacher/labs/{lab_id}/features` | Teacher, admin | Body: some of `{upload, web, code, deep}` as `on`, `off` or `inherit` (follow the class). The ones left out keep their choice. Returns the lab with `feature_overrides` (its own choices) and `features` (what students may use there now). |
| DELETE | `/api/teacher/labs/{lab_id}` | Teacher, admin | Soft-deletes an own lab (an admin: any lab). |
| POST | `/api/teacher/labs/{lab_id}/test-session` | Teacher | Opens a test-drive session (`is_test`) in an own lab. The teacher chats as a preview, without being a member. The session is left out of analytics and of the citation report. |
| GET | `/api/teacher/rule-items?level=&target_id=[&class_id=]` | Teacher | The rules of a class, a lab, or a student in one class (`class_id`, required for a student), in their order. `403` outside the teacher's classes. |
| POST | `/api/teacher/rule-items` | Teacher | Adds a rule: `{level, target_id, class_id?, text, is_active, source?, position?}`. Text 1–1000 characters, not blank. `source` is `b:<key>` (a ready-made rule) or `m:<saved rule id>` (one of the teacher's saved rules), else `422 unknown_rule_source`. `position` puts it at that place in the list (an undone delete); default last. At most 30 rules per class, lab or student (`409 too_many_rules`). |
| PATCH | `/api/teacher/rule-items/{item_id}` | Teacher | Changes `text`, `is_active` and/or `source` (only when sent; `null` unlinks). `404` for a rule outside the teacher's classes. |
| DELETE | `/api/teacher/rule-items/{item_id}` | Teacher | Deletes a rule. |
| POST | `/api/teacher/rule-items/check` | Teacher | `{level, target_id, class_id?}`. The model reads the active rules of that list and returns the pairs that contradict: `{conflicts: [{positions: [1, 3], rule_ids, reason}]}`. `positions` are the numbers the page shows (switched-off rules keep their number). Fewer than two active rules: no model call, no conflict. One call with greedy decoding, not charged to a quota; `503 rule_check_unavailable` when the model fails. Nothing is stored. |
| GET | `/api/teacher/rule-presets` | Teacher | Ready-made rules (`key`, `name`, `text`). The page adds one with `source: "b:<key>"`, and a second click removes it. |
| GET | `/api/teacher/skill-presets` | Teacher | The teacher's own saved rules, "My rules" (`id`, `name`, `content`). |
| POST | `/api/teacher/skill-presets` | Teacher | Saves a rule (`name`, `content`). |
| PUT | `/api/teacher/skill-presets/{preset_id}` | Teacher | Updates an own saved rule. The rules added from it (`source: "m:<id>"`) whose text was not edited since follow the new text, in all the teacher's classes. |
| DELETE | `/api/teacher/skill-presets/{preset_id}` | Teacher | Deletes an own saved rule. Rules already added from it are kept, with `source` set to null. |
| GET | `/api/teacher/chat-history` | Teacher | Chat history. Filters `?class_id=`, `?lab_id=`, `?student_id=`, `?session_id=`. It includes the sessions that students deleted; each row has `is_deleted`. |
| GET | `/api/teacher/analytics/classes/{class_id}/details` | Teacher, admin | The Analytics tab, for the class or one lab (`?lab_id=`). It has an overview (members, active in 7 days, questions, conversations, thumbs, students at their limit today), answers by mode, questions per day and per weekday and hour over 30 days (Paris time), every student (questions, conversations, last active, today's usage and limit), the latest 20 thumbs-down questions and the latest 20 answers that failed or stopped early. Teacher test sessions are left out. An admin may read any class (the admin Classes page); `404` when the lab is not in the class. |
| GET | `/api/teacher/labs/{lab_id}/citation-report` | Teacher | Which course files, pages and passages the research answers of the lab cite, with the students and their questions. It also lists the published files that no answer cited, the questions without a verified answer, and the answers a student marked down. `?start=YYYY-MM-DD&end=YYYY-MM-DD` (days included, UTC; `422` if the end is before the start). Test drives are left out. Sessions that students deleted are kept. The shape is in `backend/app/services/citation_report.py`. |
| GET | `/api/teacher/classes/{class_id}/citation-report` | Teacher | The same report for all labs of the class together; a class-wide file counts once. `labs` lists the labs, and each question carries its `lab_id`. Same `start`/`end` and `422` as the lab report. |
| POST | `/api/teacher/labs/{lab_id}/reports` | Teacher | Starts an AI report on the lab, or on one student of it (`{"student_id"}`). Returns `202` and writes the report in the background (about 30 s). While a report of the same lab and student is running, that one is returned. `404` when the student has no conversation in the lab. `503 agent_model_not_configured`. |
| GET | `/api/teacher/labs/{lab_id}/reports` | Teacher | The latest report on the lab, or on one student (`?student_id=`): `{"report": {id, status, error, content, facts, model, created_at, finished_at}}`, or `{"report": null}`. `status` is `running`, `done` or `failed`. `content` is Markdown. `facts` holds the counted numbers the report is based on. A report that still runs after 10 minutes is shown as failed (`interrupted`). |
| POST | `/api/teacher/labs/{lab_id}/documents` | Teacher | Uploads a course PDF for this lab. Multipart form. The file is the only field that matters (the language is found from the text). `shared` (older clients) makes the file class-wide. A new file is read by MinerU and indexed, then published at once when every page was read. Otherwise it stays a draft that the teacher publishes. To replace a document, send `mode=replace`, `replace_document_id` and `expected_generation`. Errors: `400 only_pdf` for another format (`odp_not_supported` for ODP), `413 file_too_large` (limit 200 MB), `400 invalid_pdf` or `pdf_password_or_page_limit` (at most 150 pages, no password), `409 structured_ingest_disabled` when the ingestion flags are off. Sending the same file again to the same document returns `200` and creates nothing. |
| POST | `/api/teacher/classes/{class_id}/documents` | Teacher | Uploads a class-wide PDF, which every lab of the class reads (for example the course handout). Same form and errors as the lab upload, without `shared`. |
| GET | `/api/teacher/labs/{lab_id}/documents` | Teacher | The documents a lab uses, with status, summary and `ingest_report`: the class-wide documents first, then the lab's own. Each row has `scope` (`class` or `lab`). While a PDF waits or is read, `latest_revision` also has `jobs_ahead` (reading jobs that the worker takes first) or `progress` (`{done, total, seconds_left}`: pages read; `seconds_left` is `null` before 3 pages). |
| GET | `/api/teacher/labs/{lab_id}/tp-text` | Teacher | Which of the lab's own files the tutor reads in full with every question in Search and Direct, and which it only searches: `{limit_chars, percent, context_tokens, left_out, files: [{document_id, filename, pages, chars, in_full}]}`. The same rule as the chat (whole files, the shortest first, within `TP_CONTEXT_PERCENT` of the model context and 120 pages); `chars` is estimated from the stored text. The Documents tab shows a warning when `left_out` > 0. |
| GET | `/api/teacher/classes/{class_id}/documents` | Teacher | The class-wide documents of a class. Same fields as above, with `scope` = `class`. |
| DELETE | `/api/teacher/documents/{document_id}` | Teacher | Deletes a document. For a structured document the query `expected_generation` is required (`422 expected_generation_required`), and the answer is `202 {"document_id", "state": "pending"}`: the stored files and chunks are removed in the background. |

## Admin

This area covers users, classes, the model settings, data pruning and global analytics. Admin endpoints have no ownership check. For the labs, students and analytics of one class, to rename or lock a lab, and to delete a class or a lab, the admin page calls the teacher routes marked "Teacher, admin" in [Teacher](#teacher); the admin has no copy of them.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/admin/users` | Admin | All users that are not deleted. |
| POST | `/api/admin/users` | Admin | Creates one user. |
| POST | `/api/admin/users/import` | Admin | Imports users from a CSV file. A dry run by default. `?force=true` applies it. |
| PUT | `/api/admin/users/{user_id}/role` | Admin | Changes the role (student, teacher, admin). An admin cannot change their own role (`400 cannot_change_own_role`). |
| PUT | `/api/admin/users/{user_id}/quota` | Admin | Sets the daily token quota, and the weekly one when `weekly_token_quota` is given. |
| POST | `/api/admin/users/{user_id}/quota-refill` | Admin | Gives any user a full day and week again (usage kept). |
| PUT | `/api/admin/users/{user_id}/password` | Admin | No body. Resets the password to `RESET_PASSWORD` (default `123456`) and sets `must_change_password`: until the user chooses a new password, every other route answers `403 password_change_required`, except `/api/auth/me` and `/api/auth/change-password`. |
| DELETE | `/api/admin/users/{user_id}` | Admin | Soft-deletes a user. An admin cannot delete their own account (`400 cannot_delete_self`), so one admin always stays. When no admin can sign in, `scripts/make_admin.py` gives an account the role again on the server. |
| GET | `/api/admin/classes` | Admin | All classes (`?skip=`, `?limit=`, at most 500). Each class has its owner and counts: `teacher_username`, `teacher_ok` (false when the owner was deleted or is no longer a teacher or admin, so nobody can manage the class until it is transferred), `lab_count`, `student_count`, `questions_7d` and `last_active`. Teacher test sessions are not counted. |
| PUT | `/api/admin/classes/{class_id}/transfer` | Admin | Gives the class to another teacher (`teacher_id`). |
| GET | `/api/admin/documents` | Admin | Every course document of every class, newest upload first: class, lab, uploader, status and the newest revision's state and error code. |
| GET | `/api/admin/labs` | Admin | All labs. |
| GET | `/api/admin/analytics` | Admin | Platform-wide token use: totals, `active_users_7d` and a daily breakdown over 30 days. |
| GET | `/api/admin/llm/config` | Admin | The model settings: the main model, and the embedding, rerank, agent and vision slots, the research limits (with `history_context_percent`), the token weights `token_alpha` and `token_beta`, and `mineru_server_url` with `mineru_api_key`. The keys are the ones the model servers check (see [MODEL_SERVERS.md](MODEL_SERVERS.md#the-key)); the reranker call sends `rerank_api_key` as a bearer key. An empty value means the slot uses the main model (the vision slot is off instead). It also returns, read-only, `research_limits` (default, minimum and maximum of each limit), `features` (`research_agent`, `structured_documents`, `pdf_documents`: the deployment flags) and `mineru_default_url` (the `PDF_MINERU_SERVER_URL` of the backend). |
| PUT | `/api/admin/llm/config` | Admin | Changes the model settings without a restart. Only the fields that are sent are written (the page sends only what changed). `base_url`, `api_key` and `model` may be left out but not sent empty (`422`). `rerank_enabled` is `on` or `off`; `off` turns reranking off and keeps `rerank_url`. A research limit out of range gets `422`. `mineru_server_url` switches the PDF worker to another MinerU server at its next tick (empty uses the worker's own `PDF_MINERU_SERVER_URL`); `mineru_api_key` is its key (empty: the main model key). |
| GET | `/api/admin/llm/servers` | Admin | What each model server serves. One row per slot (main, agent, vision, embeddings, reranker): `slot`, `label`, `model`, `server`, `status` (`ok`, `model_missing`, `refused` when the server does not accept the key, `unreachable` or `off`), `served` (model names the server lists) and `max_model_len` (the context length of the configured model, when the server reports it). The servers are asked with a 3-second timeout. |
| POST | `/api/admin/llm/servers/check` | Admin | Tests one server with the values on the page, saved or not: `{url, model, api_key}`. Returns `{status, served, max_model_len}`; `status` is `ok`, `model_missing`, `refused` (the key), `unreachable` or `invalid_url`. An empty `model` (the MinerU server) is `ok` when the server answers. 3-second timeout. |
| DELETE | `/api/admin/sessions/{session_id}` | Admin | Deletes a session and its messages for good. |
| POST | `/api/admin/maintenance/prune` | Admin | Deletes sessions and messages older than `older_than_days`. With `dry_run: true` it only counts them. |

## Files students upload in the chat

Not course files: no MinerU and no index (see `backend/app/services/chat_file_service.py`). An image (PNG, JPEG, GIF, WebP) is kept as it is. A PDF keeps the text of its text layer; a scanned PDF has `has_text: false`. A text file (code, notes, data: `.py`, `.c`, `.java`, `.txt`, `.md`, `.csv`, `.json`...) keeps its text. 10 MB at most. The type is read from the first bytes, not from the name.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/files` | Any user | Multipart `file` and `lab_id`. The user must be in the lab's class (or own it: the test drive), the lab open, and its `upload` feature on (`403 feature_off`; exam mode turns it off). Returns `{id, lab_id, session_id, filename, mime, size, kind, has_text, text_chars, created_at}` with `201`. Errors: `413 file_too_large`, `415 unsupported_type`, `422 empty_file` or `unreadable_pdf`. |
| GET | `/api/student/files` | Any user | The user's own files, not hidden, newest first (100 at most). Optional `?lab_id=`. |
| DELETE | `/api/student/files/{file_id}` | Any user | Hides an own file: it leaves the list and cannot be attached again. The class teacher still sees it, like a deleted conversation. Returns `204`. |
| GET | `/api/files/{file_id}/content` | Owner, class teacher, admin | The file itself (`inline`). The owner loses access once it is hidden; others get `404`. |

## Notifications

The bell, for every role. A notification is written where its event happens; the list holds `kind` and `payload`, and the page writes the text in the user's language. Which events write which kinds is in [DATABASE.md](DATABASE.md) (`notifications`).

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/notifications` | Any user | The latest 50, newest first: `{items: [{id, kind, payload, created_at, read}], unread}`. |
| POST | `/api/notifications/read` | Any user | Body `{ids: [...]}` marks those as read (only the user's own); with no `ids`, all. Returns `{marked}`. |

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
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/assets/{asset_id}` | Teacher | A stored image or file of the revision. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/preview` | Teacher | The PDF used to show the revision. |
| GET | `/api/teacher/documents/{document_id}/revisions/{revision_id}/source` | Teacher | The file the teacher uploaded. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/pages/{page_id}` | Any user | One page with its blocks. Also says whether a newer revision exists (`newer_revision_available`). Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/pages/{page_no}/image` | Any user | One page of the revision's PDF as a PNG (at most 1400 px), for the cited-page view. A page the revision does not have gets `404`. Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/assets/{asset_id}` | Any user | A stored image of the revision. Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/revisions/{revision_id}/preview` | Any user | The PDF used to show the revision. Needs `?session_id=`. |
| GET | `/api/documents/{document_id}/pdf` | Any user | The complete course PDF, with the session's access checked again. Needs `?session_id=`. Optional `?revision_id=`. |

The five `/api/documents/` routes above answer `403 exam_mode` when the session's class is in exam mode and the file is not allowed in the exam: a student cannot open it, even pages cited in earlier answers. The owning teacher's test session still opens them. Stored citations are kept and show again when exam mode is off.

## Health

This area has one endpoint that tells whether the backend runs.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/health` | none | Returns `{"status": "ok", ...}` when the server is up. Docker health checks use it. |
