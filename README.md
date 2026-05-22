# Edu-LLM: v3 Lean MVP Architecture
---

## 1. Project Overview

**Edu-LLM (v3 Lean MVP)** is a streamlined, full-stack educational platform designed for a controlled classroom environment (approx. 20 students). It features a strict 3-tier RBAC system, real-time DB-based rate limiting, and a dynamic "Teacher Rules" injection mechanism. To minimize infrastructure overhead, all message queueing (Redis) has been removed in favor of direct API streaming with asynchronous background database writes.

---

## 2. Directory Tree


```

```text
File architecture_v3_lean_mvp.md successfully generated.

```text
polytech_ai_tp/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app entry point (with BackgroundTasks)
│   │   ├── database.py              # SQLAlchemy engine & session
│   │   ├── models.py                # ORM models (User, TeacherRule, Session, Message, UsageStat)
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   ├── auth.py                  # JWT creation & get_current_user deps
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── auth.py              # POST /api/auth/login
│   │       ├── admin.py             # /api/admin/* (Users & Quota management)
│   │       ├── teacher.py           # /api/teacher/* (Rules & Audit)
│   │       ├── student.py           # /api/student/* (Session management)
│   │       └── chat.py              # POST /api/chat/stream (Direct SSE + DB Rate Check)
│   ├── .env                         # LLM_BASE_URL, DATABASE_URL, JWT_SECRET
│   └── requirements.txt
├── frontend/
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── App.jsx                  # React Router setup
│   │   ├── pages/
│   │   │   ├── Login.jsx            
│   │   │   ├── Chat.jsx             
│   │   │   ├── Teacher.jsx          
│   │   │   └── Admin.jsx            
│   │   └── components/
│   │       ├── ProtectedRoute.jsx   
│   │       └── SessionSidebar.jsx   
│   └── package.json
├── docker-compose.yml               # PostgreSQL service ONLY (No Redis)
└── architecture_v3_lean_mvp.md

```

---

## 3. Database Schema (SQLAlchemy - PostgreSQL)

The system relies on **5 core tables** optimized for the new decoupled rule injection and token tracking.

### 1. Users Table

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Integer | Primary Key |
| username | String | Unique, Not Null |
| hashed_password | String | bcrypt. Not Null |
| role | Enum(student, teacher, admin) | Not Null |
| daily_token_quota | Integer | Hard limit (e.g., 50000). Set by Admin. |
| is_deleted | Boolean | Default: False — Soft delete flag |

### 2. Teacher Rules Table (NEW)

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| teacher_id | FK | → Users.id |
| student_id | FK | → Users.id (Allows targeted pedagogical intervention) |
| rules_json | JSON/Text | The restrictive prompts/instructions designed by the teacher. |
| is_active | Boolean | Default: True. Toggle for applying these rules. |

### 3. Usage Stats Table (NEW)

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| user_id | FK | → Users.id |
| date | Date | YYYY-MM-DD format for daily aggregation |
| tokens_used | Integer | Total tokens consumed on this date |
| request_count | Integer | Number of LLM requests made on this date |

### 4. Sessions Table

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| user_id | FK | → Users.id |
| applied_rule_id | FK | → TeacherRules.id — **Nullable**. `NULL` = normal session, value = session was conducted under this specific teacher rule. Set by Prompt Controller at session creation time. |
| is_deleted | Boolean | Default: False |
| created_at | Timestamp | Timezone-aware (UTC) |
### 5. Messages Table

| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| session_id | FK | → Sessions.id |
| sender | Enum(user, llm) | Not Null |
| content | Text | Not Null |
| prompt_tokens | Integer | Tracked after generation |
| completion_tokens | Integer | Tracked after generation |
| total_tokens | Integer | prompt_tokens + completion_tokens |
| created_at | Timestamp | Timezone-aware (UTC) |

---

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

### A. Auth & Admin Operations

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | — | Validate credentials. Return JWT containing ONLY `user_id`. |
| GET | `/api/admin/users` | require_admin | List users, roles, and current `daily_token_quota`. |
| PUT | `/api/admin/users/{id}/quota` | require_admin | Update a specific student's daily token quota. |
| DELETE | `/api/admin/users/{id}` | require_admin | **Soft Delete**: Set `is_deleted = True`. |

### B. Teacher Audit & Control

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| GET | `/api/teacher/students` | require_teacher | List active students + current daily token consumption. |
| GET | `/api/teacher/sessions/{student_id}` | require_teacher | Fetch full chat history for a specific student. |
| POST | `/api/teacher/rules` | require_teacher | Create/Update targeted teaching rules in `teacher_rules`. |
| PUT | `/api/teacher/rules/{rule_id}/toggle` | require_teacher | Toggle `is_active` status for a specific rule. |
| GET | `/api/teacher/rules` | require_teacher | List all rules created by this teacher. |

### C. Student Flow (Session & Chat)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/sessions` | get_current_user | Create a new chat session. |
| GET | `/api/student/sessions` | get_current_user | List own active sessions. |
| GET | `/api/student/sessions/{session_id}` | get_current_user | Fetch message history. **IDOR check required.** |
| POST | `/api/chat/stream` | get_current_user | Core chat endpoint. See full logic below. |

---

## 6. POST `/api/chat/stream` — Full Logic Spec (v3)

**Payload:**

```json
{
  "session_id": "uuid",
  "message": "string"
}

```

**Step-by-step Logic:**

1. **IDOR Security & Rate Limit Check (DB Direct)**
* Verify `session.user_id == current_user.id` AND `session.is_deleted == False`.
* Query `usage_stats` for today. If `tokens_used >= current_user.daily_token_quota`, reject with `429 Too Many Requests`.


2. **Prompt Controller**
* Query `teacher_rules` for this `student_id` where `is_active == True`.
* Retrieve latest 3-5 messages from `messages` for sliding-window context.
* Construct payload: inject active teacher rules into the system prompt, append chat history, and add the new user message.


3. **Direct Proxy (No Redis)**
* Proxy payload directly to the LLM engine using standard `AsyncOpenAI` client (works for Ollama MVP & Mistral API).
* Ensure `LLM_BASE_URL` and `LLM_API_KEY` are read from `.env`.


4. **SSE Streaming & Token Counting**
* Write an `async generator` yielding tokens via SSE (`text/event-stream`).
* Track `completion_tokens` by counting yielded chunks or reading the final usage stats from the LLM provider's chunk stream.
* Use `await request.is_disconnected()` to break early if the client drops.


5. **Safe Async Write (`BackgroundTasks`)**
* Use FastAPI's `BackgroundTasks` to execute DB writes *after* the stream finishes.
* Task must:
1. Insert the User message.
2. Insert the LLM generated message (with exact `prompt_tokens` and `completion_tokens`).
3. Upsert `usage_stats` (increment `tokens_used` and `request_count` for today).





---

## 7. Execution Instruction for AI Agent

Begin by scaffolding the Lean MVP v3:

* [ ] Initialize standard directory structure.
* [ ] `.env` configuration (with `LLM_BASE_URL`, `LLM_API_KEY`, `DATABASE_URL`, `JWT_SECRET`).
* [ ] `docker-compose.yml` (PostgreSQL ONLY, remove Redis).
* [ ] SQLAlchemy models (Update `Users`, add `TeacherRule`, add `UsageStat`, modify `Message`).
* [ ] Implement direct DB-based rate limiter dependency.
* [ ] Implement FastAPI router structure with DB-driven RBAC Depends chain.
* [ ] Implement `POST /api/chat/stream` using FastAPI `BackgroundTasks` for token-aware writing and direct SSE streaming.
* [ ] Add dummy response returns for Admin and Teacher CRUD endpoints.

**Stop and wait for review once the basic skeleton is created.**