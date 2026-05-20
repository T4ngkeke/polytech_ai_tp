# polytech_ai_tp
---

## 1. Project Overview

**Edu-LLM** is a full-stack educational platform that lets teachers control and audit an AI assistant used by students. It features a 3-tier RBAC system (Admin / Teacher / Student), JWT authentication, and a dynamic "Poison Prompt" injection mechanism for pedagogical experiments.

---

## 2. Directory Tree

```
polytech_ai_tp/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app entry point
│   │   ├── database.py              # SQLAlchemy engine & session
│   │   ├── models.py                # ORM models (User, Session, Message)
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   ├── auth.py                  # JWT creation & get_current_user deps
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── auth.py              # POST /api/auth/login
│   │       ├── admin.py             # /api/admin/* routes
│   │       ├── teacher.py           # /api/teacher/* routes
│   │       ├── student.py           # /api/student/* routes
│   │       └── chat.py              # POST /api/chat/stream (SSE)
│   ├── .env                         # LLM_BASE_URL, DATABASE_URL, JWT_SECRET
│   └── requirements.txt
├── frontend/
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── main.jsx
│   │   ├── App.jsx                  # React Router setup
│   │   ├── api/
│   │   │   └── client.js            # Axios/fetch wrappers + JWT header
│   │   ├── pages/
│   │   │   ├── Login.jsx            # /login — public
│   │   │   ├── Chat.jsx             # /chat  — student
│   │   │   ├── Teacher.jsx          # /teacher — teacher
│   │   │   └── Admin.jsx            # /admin — admin
│   │   └── components/
│   │       ├── ProtectedRoute.jsx   # Role-based route guard
│   │       └── SessionSidebar.jsx   # Session list sidebar
│   └── package.json
├── docker-compose.yml               # PostgreSQL service
├── system_architecture.md
└── README.md
```

---

## 3. Database Schema (SQLAlchemy)

The system relies on **3 core tables**. Implement these strictly.

### Users Table

| Column           | Type                          | Constraints / Notes              |
|------------------|-------------------------------|----------------------------------|
| id               | UUID / Integer                | Primary Key                      |
| username         | String                        | Unique, Not Null                 |
| hashed_password  | String                        | bcrypt. Not Null                 |
| role             | Enum(student, teacher, admin) | Not Null                         |
| is_deleted       | Boolean                       | Default: False — Soft delete flag|

### Sessions Table

| Column           | Type     | Constraints / Notes                                   |
|------------------|----------|-------------------------------------------------------|
| id               | UUID / Integer | Primary Key                                     |
| user_id          | FK       | → Users.id                                           |
| is_poison_active | Boolean  | Default: False — Master switch for interference       |
| poison_prompt    | Text     | Default: Null — Custom prompt written by teacher      |
| is_deleted       | Boolean  | Default: False — Soft delete flag for hiding sessions |
| created_at       | Timestamp| Timezone-aware (UTC)                                  |

### Messages Table

| Column     | Type                  | Constraints / Notes         |
|------------|-----------------------|-----------------------------|
| id         | UUID / Integer        | Primary Key                 |
| session_id | FK                    | → Sessions.id               |
| sender     | Enum(user, llm)       | Not Null                    |
| content    | Text                  | Not Null                    |
| created_at | Timestamp             | Timezone-aware (UTC)        |

---

## 4. RBAC Authorization (JWT + FastAPI Depends)

### Security Rules

> **Stale Token Prevention:**
> The JWT payload MUST ONLY contain `user_id`.
> Do NOT store `role` in the JWT.
> The Depends function must query the Users table in real-time to fetch the
> current role.

> **Soft Delete Check:**
> If the user fetched from DB has `is_deleted == True`, immediately reject
> with `401 Unauthorized`.

### Dependency Chain

| Depends Function  | Who Gets In                            | Rejection |
|-------------------|----------------------------------------|-----------|
| `get_current_user`| Any valid JWT whose user is not deleted| 401       |
| `require_teacher` | role == `teacher` OR `admin`           | 403       |
| `require_admin`   | role == `admin` ONLY                   | 403       |

---

## 5. API Routing Contract

### A. Auth & Admin Operations

| Method | Endpoint                           | Auth            | Description                                                  |
|--------|------------------------------------|-----------------|--------------------------------------------------------------|
| POST   | `/api/auth/login`                  | —               | Validate credentials. Return JWT containing ONLY `user_id`. |
| GET    | `/api/admin/users`                 | require_admin   | List all users, including soft-deleted (with status flag).   |
| POST   | `/api/admin/users`                 | require_admin   | Create new users.                                            |
| PUT    | `/api/admin/users/{user_id}/role`  | require_admin   | Change a user's role.                                        |
| DELETE | `/api/admin/users/{user_id}`       | require_admin   | **Soft Delete**: Set `is_deleted = True`.                    |
| DELETE | `/api/admin/users/{user_id}/hard`  | require_admin   | **Hard Delete**: Physically remove user + cascade sessions/messages. |

---

### B. Teacher Audit & Control

| Method | Endpoint                                    | Auth            | Description                                           |
|--------|---------------------------------------------|-----------------|-------------------------------------------------------|
| GET    | `/api/teacher/students`                     | require_teacher | List all active students (`is_deleted == False`).     |
| GET    | `/api/teacher/sessions/{student_id}`        | require_teacher | Fetch full chat history for a specific student.       |
| PUT    | `/api/teacher/sessions/{session_id}/poison` | require_teacher | Toggle `is_poison_active` and update `poison_prompt`. |

---

### C. Student Core Flow (Session & Chat)

| Method | Endpoint                               | Auth             | Description                                      |
|--------|----------------------------------------|------------------|--------------------------------------------------|
| POST   | `/api/student/sessions`               | get_current_user | Create a new chat session. Returns `session_id`. |
| GET    | `/api/student/sessions`               | get_current_user | List own active sessions (`is_deleted == False`).|
| GET    | `/api/student/sessions/{session_id}`  | get_current_user | Fetch message history. **IDOR check required.**  |
| POST   | `/api/chat/stream`                    | get_current_user | Core chat endpoint. See full logic below.        |

---

### POST `/api/chat/stream` — Full Logic Spec

**Payload:**
```json
{
  "session_id": "uuid",
  "message": "string"
}
```

**Step-by-step Logic:**

1. **IDOR Security Check**
   Verify `session.user_id == current_user.id` AND `session.is_deleted == False`.
   Reject with `403` if check fails.

2. **Prompt Controller**
   Construct the system message using a standard `messages[]` array template.
   If `is_poison_active == True`, inject `poison_prompt` into the system
   template cleanly.

3. **Dynamic Proxy**
   Proxy payload to LLM engine.
   **CRITICAL:** Do NOT hardcode the LLM URL.
   Read `LLM_BASE_URL` from `.env`.

4. **Resilient Stream Buffer**
   Write an `async generator` that yields tokens to the frontend via SSE
   (`text/event-stream`).
   - Declare a `full_response_buffer: str = ""`.
   - **Resilience & Timeout:** Wrap the LLM inference call in
     `asyncio.timeout()` to prevent hanging.
   - **Disconnect Check:** Periodically `await request.is_disconnected()`
     inside the yield loop to break early if client drops.

5. **Safe Async Write**
   Wrap DB persistence in a `finally` block or `BackgroundTask`.
   It MUST execute even if the stream was cancelled or disconnected,
   saving whatever text was accumulated in `full_response_buffer`.

---

## 6. Frontend Routing Contract (React Router)

Protected routes based on real-time role state from JWT + DB.

| Route      | Role      | Description                                                     |
|------------|-----------|-----------------------------------------------------------------|
| `/login`   | Public    | Simple login form.                                             |
| `/chat`    | Student   | Chatbot UI with session sidebar. Markdown + syntax highlighting.|
| `/teacher` | Teacher   | Split view: Student list + Audit panel + Poison control.       |
| `/admin`   | Admin     | User CRUD table. Soft/Hard delete UI. Role management.         |

---

## 7. Execution Instruction for AI Agent

Please read this contract carefully.

Begin by scaffolding:
- [ ] Full directory structure
- [ ] `.env` configuration file (with `LLM_BASE_URL`, `DATABASE_URL`, `JWT_SECRET`)
- [ ] `docker-compose.yml` (PostgreSQL setup)
- [ ] SQLAlchemy database models (all 3 tables, with timezone-aware timestamps)
- [ ] FastAPI router structure with correct DB-driven RBAC Depends chain
- [ ] Full logic implementation for `POST /api/chat/stream` including IDOR check,
      prompt controller, SSE streaming, timeout, disconnect guard, and finally-block
      DB write
- [ ] Placeholder returns for all other non-core endpoints

**Stop and wait for review once the basic skeleton is created.**