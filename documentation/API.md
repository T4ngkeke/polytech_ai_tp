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
| GET | `/api/admin/llm/config` | require_admin | Read the current model-routing config: the main LLM, plus the embedding / rerank / ingest / router / agent / vision slots, the cost weights and the budgets. |
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
| POST | `/api/admin/maintenance/prune` | require_admin | Bulk hard-delete sessions/messages older than X days. Also prunes `RouterQueryLog` + `AgentTraceLog`. |
| GET | `/api/admin/ingestion/jobs` | require_admin | Inspect the ingestion queue (status, attempts, errors). |
| GET | `/api/admin/health/degradations` | require_admin | Health panel: per-fallback counts (router / embedding / rerank / all_filtered) over a recent window (`?window_minutes=`). |

### B. Teacher Audit & Control

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | require_teacher | Create class and generate 6-char `invite_code`. |
| GET | `/api/teacher/classes` | require_teacher | List own classes (name, invite code, student count). |
| PUT | `/api/teacher/classes/{class_id}` | require_teacher | Rename/update class details (ownership enforced). |
| DELETE | `/api/teacher/classes/{class_id}` | require_teacher | Soft-delete own class. |
| POST | `/api/teacher/classes/{class_id}/reset-code` | require_teacher | Generate new invite code (invalidating the old one). |
| GET | `/api/teacher/students` | require_teacher | List every student across the teacher's own classes. |
| GET | `/api/teacher/classes/{class_id}/students` | require_teacher | List the students in one owned class. |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}` | require_teacher | Kick/Remove a student from the class. |
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
| GET | `/api/teacher/analytics/classes/{class_id}` | require_teacher | Hierarchical token usage: class → lab → student breakdown. |
| POST | `/api/teacher/labs/{lab_id}/documents` | require_teacher | Upload course material (**PDF / Markdown / HTML**). Multipart form; the file is the only required field. Optional `language` (BM25 config, default `fr`) and `shared` (class-wide → `lab_id=NULL`). Stored + enqueued. |
| GET | `/api/teacher/labs/{lab_id}/documents` | require_teacher | List documents + status + summary + `ingest_report`. |
| GET | `/api/teacher/documents/{document_id}/chunks` | require_teacher | Paginated chunk text + `page_no` (+ generated `context`). |
| PUT | `/api/teacher/documents/{document_id}/chunks/{chunk_id}` | require_teacher | Correct a chunk's text; re-embeds + rebuilds the BM25 `tsv`; sets `edited_by_teacher`. |
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
| GET | `/api/student/classes` | get_current_user | List all joined classes (with labs). |
| GET | `/api/student/classes/{class_id}/labs` | get_current_user | List active labs for a joined class. |
| DELETE | `/api/student/classes/{class_id}/leave` | get_current_user | Voluntarily leave a joined class. |
| POST | `/api/student/labs/{lab_id}/sessions` | get_current_user | Create a new Chat Session specifically within a Lab. |
| GET | `/api/student/sessions` | get_current_user | List own sessions (optional `?lab_id=` filter). |
| GET | `/api/student/sessions/{session_id}` | get_current_user | Fetch message history. **IDOR check required.** |
| PUT | `/api/student/sessions/{session_id}` | get_current_user | Rename a chat session title (cosmetic only). |
| DELETE | `/api/student/sessions/{session_id}` | get_current_user | **Soft-delete (hide)** own session: sets `is_deleted=True`. Hidden from the student; retained and teacher/admin-visible. **IDOR check required.** |
| GET | `/api/student/usage` | get_current_user | Today's token usage vs. daily quota. |
| POST | `/api/chat/stream` | get_current_user | Core chat endpoint. Scoped by `session_id`. LangGraph agent, SSE streaming. |
| POST | `/api/chat/messages/{message_id}/feedback` | get_current_user | 👍/👎 on an assistant message (body `{feedback: up\|down}`). A student may only rate a message in a session they own. |

