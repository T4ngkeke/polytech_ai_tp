# Edu-LLM: v6 Class-Lab Architecture
---

## 1. Project Overview

**Edu-LLM (v6 Class-Lab Architecture)** is a streamlined, full-stack educational platform
designed for a controlled classroom environment. It features a strict 3-tier
RBAC system, an Invite-Code based zero-friction joining workflow, real-time DB-based rate limiting,
and a powerful **Three-Tier Rule Injection** mechanism (Class -> Lab -> Student) — all running on a minimal stack of FastAPI, PostgreSQL,
and React with no external message queue required.

**v6 Architecture Principles:**
- **Service Layer**: All business/DB logic lives in `backend/app/services/`. Routers are thin controllers only.
- **No Cross-Router Calls**: Admin and Teacher routers call the same service functions independently — no router imports another router.
- **Audit Integrity**: Students cannot delete their own sessions. All chat history is immutable and auditable by teachers.
- **Hierarchical UI**: All role dashboards use a Class → Lab tree navigation instead of flat tab lists.

---

## 2. Directory Tree

```text
polytech_ai_tp/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app entry point
│   │   ├── database.py              # SQLAlchemy engine & session
│   │   ├── models.py                # ORM models (Users, Classes, Labs, Rules, etc.)
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   ├── auth.py                  # JWT creation & get_current_user deps
│   │   ├── services/                # [NEW v6] Business logic layer
│   │   │   ├── __init__.py
│   │   │   ├── class_service.py     # Class CRUD + invite code generation
│   │   │   ├── lab_service.py       # Lab CRUD + active toggle
│   │   │   ├── rule_service.py      # Rule upsert + queries
│   │   │   ├── analytics_service.py # Hierarchical token aggregation
│   │   │   └── session_service.py   # Session CRUD (no student delete)
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── auth.py              # POST /api/auth/signup, /api/auth/login
│   │       ├── admin.py             # /api/admin/* — calls services, no ownership checks
│   │       ├── teacher.py           # /api/teacher/* — calls services, ownership-checked
│   │       ├── student.py           # /api/student/* — join, sessions (no delete)
│   │       └── chat.py              # POST /api/chat/stream (SSE + DB Rate Check)
│   ├── .env                         # DATABASE_URL, JWT_SECRET (LLM configs moved to DB)
│   ├── requirements.txt
│   └── Dockerfile                   # Python 3.12-slim FastAPI build
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── App.jsx                  # React Router setup
│   │   ├── pages/
│   │   │   ├── Login.jsx            # Login + link to Register
│   │   │   ├── Register.jsx         # [NEW v6] Student self-registration
│   │   │   ├── Chat.jsx             # Student chat (hierarchical sidebar + no delete)
│   │   │   ├── Teacher.jsx          # Teacher workspace (tree + right panel)
│   │   │   └── Admin.jsx            # Admin god-mode (5 tabs)
│   │   ├── components/
│   │   │   ├── HierarchicalSidebar.jsx  # [NEW v6] Role-aware Class→Lab tree
│   │   │   ├── MainLayout.jsx
│   │   │   ├── ProtectedRoute.jsx
│   │   │   └── UserHeader.jsx
│   │   ├── lib/
│   │   │   └── api.js               # Centralized API client
│   │   └── store/                   # Zustand stores (Auth, etc.)
│   ├── package.json
│   ├── tailwind.config.js           # Tailwind v3 config
│   ├── nginx.conf                   # Nginx reverse proxy config for prod
│   └── Dockerfile                   # Node builder + Nginx multi-stage build
└── docker-compose.yml               # Postgres + Backend + Frontend Orchestration
```

---

## 3. Database Schema (SQLAlchemy - PostgreSQL)

The system relies on core tables optimized for the decoupled rule injection, hierarchical class-lab structure, and token tracking.

### 1. SystemConfigs
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| key | String | Unique. e.g., `LLM_BASE_URL`, `LLM_MODEL` |
| value | String | The configuration value |
| updated_at | Timestamp | For tracking when admin changed configs |

### 2. Users
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Integer | Primary Key |
| username | String | Unique, Not Null |
| hashed_password | String | bcrypt. Not Null |
| role | Enum(student, teacher, admin) | Not Null |
| daily_token_quota | Integer | Hard limit (e.g., 50000). Set by Admin. |
| is_deleted | Boolean | Default: False — Soft delete flag |

### 3. Classes
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| name | String | Name of the class |
| teacher_id | FK | → Users.id (Must be a teacher) |
| invite_code | String | Unique, 6-character short code for zero-friction joining |
| is_deleted | Boolean | Default: False — Soft delete flag for historical retention |
| created_at | Timestamp | Timezone-aware (UTC) |

### 4. Class_Students
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| class_id | FK | → Classes.id |
| student_id | FK | → Users.id |
| joined_at | Timestamp | Timezone-aware (UTC) |
*Primary Key is composite: (class_id, student_id)*

### 5. Labs
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| class_id | FK | → Classes.id |
| name | String | E.g., "Python Basics Lab 1" |
| is_active | Boolean | Default: True. Set to False to lock lab (Read-only) |
| is_deleted | Boolean | Default: False — Soft delete flag |
| created_at | Timestamp | Timezone-aware (UTC) |

### 6. Rules (3-tier polymorphic)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| level | Enum | `class`, `lab`, `student` |
| target_id | UUID / Int | The ID of the Class, Lab, or Student this applies to |
| rules_text | Text | The restrictive prompts/instructions designed by the teacher |
| is_active | Boolean | Default: True. Toggle for applying these rules |

### 7. Sessions
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| user_id | FK | → Users.id |
| lab_id | FK | → Labs.id. Sessions are tightly scoped to a specific lab |
| title | String | Optional display name, renameable by student |
| is_deleted | Boolean | Default: False. **Only admin/teacher can set True.** |
| created_at | Timestamp | Timezone-aware (UTC) |

### 8. Messages & Usage Stats
(Unchanged from previous versions. Tracks tokens used per user and per message).

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

> **v6 Rule:** Every router calls its own service layer. No router ever calls another router's functions.
> Admin routes bypass ownership checks; Teacher routes enforce ownership.

### A. Auth & Admin Operations

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/auth/signup` | — | Student self-registration (Default role: student). Fails if username exists. |
| POST | `/api/auth/login` | — | Validate credentials. Return JWT containing ONLY `user_id`. |
| GET | `/api/admin/users` | require_admin | List all non-deleted users. |
| POST | `/api/admin/users` | require_admin | Create a single new user. |
| PUT | `/api/admin/users/{id}/role` | require_admin | Change user role (e.g., student ↔ teacher). |
| PUT | `/api/admin/users/{id}/quota` | require_admin | Update user's daily token quota. |
| DELETE | `/api/admin/users/{id}` | require_admin | Soft-delete a user. |
| POST | `/api/admin/users/import` | require_admin | CSV Bulk Import. Dry-run by default. `?force=true` executes. |
| GET | `/api/admin/llm/config` | require_admin | Read current LLM config (base_url, model, api_key). |
| PUT | `/api/admin/llm/config` | require_admin | Zero-downtime update of SystemConfigs (Base URL, API Key, Model). |
| GET | `/api/admin/classes` | require_admin | God Mode: List all classes across the system (no ownership filter). |
| GET | `/api/admin/classes/{class_id}/labs` | require_admin | God Mode: List all labs in a class. |
| GET | `/api/admin/classes/{class_id}/students` | require_admin | God Mode: View all students in a specific class. |
| GET | `/api/admin/labs` | require_admin | God Mode: List all labs across the system. |
| PUT | `/api/admin/classes/{class_id}/transfer` | require_admin | Force transfer class ownership (`teacher_id`). |
| DELETE | `/api/admin/classes/{class_id}` | require_admin | Force soft-delete any class (bypasses ownership). |
| DELETE | `/api/admin/labs/{lab_id}` | require_admin | Force soft-delete any lab (bypasses ownership). |
| GET | `/api/admin/analytics` | require_admin | Global platform-wide token usage dashboard (hierarchical). |
| GET | `/api/admin/analytics/classes/{class_id}` | require_admin | Per-class hierarchical analytics (class→lab→student). |
| DELETE | `/api/admin/sessions/{session_id}` | require_admin | Hard-delete a specific session to purge inappropriate content. |
| POST | `/api/admin/maintenance/prune` | require_admin | Bulk hard-delete sessions/messages older than X days. |

### B. Teacher Audit & Control

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | require_teacher | Create class and generate 6-char `invite_code`. |
| GET | `/api/teacher/classes` | require_teacher | List own classes (name, invite code, student count). |
| PUT | `/api/teacher/classes/{class_id}` | require_teacher | Rename/update class details (ownership enforced). |
| DELETE | `/api/teacher/classes/{class_id}` | require_teacher | Soft-delete own class. |
| POST | `/api/teacher/classes/{class_id}/reset-code` | require_teacher | Generate new invite code (invalidating the old one). |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}` | require_teacher | Kick/Remove a student from the class. |
| POST | `/api/teacher/classes/{class_id}/labs` | require_teacher | Create a Lab under a Class (ownership enforced). |
| GET | `/api/teacher/classes/{class_id}/labs` | require_teacher | List labs for a class. |
| PUT | `/api/teacher/labs/{lab_id}` | require_teacher | Rename lab or toggle `is_active` (Open/Close). |
| DELETE | `/api/teacher/labs/{lab_id}` | require_teacher | Soft-delete a Lab (ownership enforced). |
| GET | `/api/teacher/rules` | require_teacher | Get existing rules. Filters: `?level=X&target_id=Y` |
| PUT | `/api/teacher/rules` | require_teacher | Create/Update Rules (Class level, Lab level, or Student level). |
| GET | `/api/teacher/chat-history` | require_teacher | Fetch chat history. Filters: `?class_id=X&lab_id=Y&student_id=Z&session_id=W` |
| GET | `/api/teacher/analytics/classes/{class_id}` | require_teacher | Hierarchical token usage: class → lab → student breakdown. |

### C. Student Flow (Session & Chat)

> **Audit Integrity Rule:** Students CANNOT delete their own sessions.
> All sessions are permanently retained for teacher audit. Only admin can purge sessions.

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
| GET | `/api/student/usage` | get_current_user | Today's token usage vs. daily quota. |
| POST | `/api/chat/stream` | get_current_user | Core chat endpoint. Scoped by `session_id`. SSE streaming. |

---

## 6. POST `/api/chat/stream` — Full Logic Spec (v6)

**Payload:**

```json
{
  "session_id": "uuid",
  "message": "string"
}
```

**Step-by-step Logic:**

1. **Security & Context Resolution**
   * Fetch Session to find `lab_id`. Verify `session.user_id == current_user.id`.
   * Verify the user has successfully joined the Class that owns this Lab.
   * Verify the Lab `is_active == True` (reject if lab is locked).

2. **Three-Tier Prompt Controller**
   * Query `rules` table for active rules matching:
     - `level=class, target_id=class_id`
     - `level=lab, target_id=lab_id`
     - `level=student, target_id=current_user.id`
   * Concatenate these rules sequentially (Class → Lab → Student) to build a robust, granular System Prompt.

3. **Dynamic LLM Proxy (Zero-Downtime)**
   * Fetch active `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` from `SystemConfigs` table.
   * Proxy payload directly to the LLM engine using standard `AsyncOpenAI` client.

4. **SSE Streaming & Safe Async Write**
   * Stream tokens via SSE (`text/event-stream`).
   * Use `BackgroundTasks` to insert User message, LLM message (with tokens), and Upsert `usage_stats`.

---

## 7. Analytics Schema — Hierarchical Response (v6)

The `ClassAnalyticsResponse` now includes a full Lab→Student breakdown:

```json
{
  "class_id": "uuid",
  "class_name": "Algorithms 2024",
  "total_tokens": 12000,
  "total_requests": 340,
  "labs": [
    {
      "lab_id": "uuid",
      "lab_name": "Python Lab 1",
      "tokens": 7000,
      "requests": 200,
      "students": [
        { "user_id": "uuid", "username": "alice", "tokens_used": 3500, "request_count": 120 },
        { "user_id": "uuid", "username": "bob",   "tokens_used": 3500, "request_count": 80 }
      ]
    },
    {
      "lab_id": "uuid",
      "lab_name": "Python Lab 2",
      "tokens": 5000,
      "requests": 140,
      "students": []
    }
  ]
}
```

---

## 8. Deployment & Run Instructions

The application is fully containerized using Docker, allowing for a single-command deployment.

### Prerequisites
- Docker and Docker Compose installed.
- (Local LLM) Ollama installed on the host machine running on `http://127.0.0.1:11434`.

### 🚀 Running with Docker (Production/Demo Mode)
To spin up the Postgres database, FastAPI backend, and Nginx/React frontend simultaneously:

```bash
docker compose up -d --build
```
- **Frontend**: Access via `http://localhost` (Port 80).
- **Backend API**: Proxied through Nginx via `/api/` or available directly at `http://localhost:8000`.
- **Database**: Port mapped on `5432`.

#### 🔐 Initialize Database (First Time Only)
After starting the containers for the first time, you MUST seed the database with initial accounts:
```bash
docker compose exec backend python -m backend.seed
```
**Default Accounts Created:**
- `admin` / `admin123`
- `teacher` / `teacher123`
- `student1` / `student123`
- `student2` / `student123`

*(Note: The backend container uses `host.docker.internal` to reach the local host's Ollama model natively without complex networking.)*

### 🛠️ Running Natively (Development Mode)
If you prefer running services outside of Docker for development:

1. **Database**: Start a Postgres server (you can use `docker-compose up -d postgres`).
2. **Backend (Run from project root)**:
   ```bash
   pip install -r backend/requirements.txt

   # Initialize the database (first time only)
   python -m backend.seed

   # Start the server
   uvicorn backend.app.main:app --reload --port 8000
   ```
3. **Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   Access the dev server at `http://localhost:5173`.