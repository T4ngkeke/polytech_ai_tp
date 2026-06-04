# Edu-LLM: v5 Class-Lab Architecture
---

## 1. Project Overview

**Edu-LLM (v5 Class-Lab Architecture)** is a streamlined, full-stack educational platform
designed for a controlled classroom environment. It features a strict 3-tier
RBAC system, an Invite-Code based zero-friction joining workflow, real-time DB-based rate limiting,
and a powerful **Three-Tier Rule Injection** mechanism (Class -> Lab -> Student) — all running on a minimal stack of FastAPI, PostgreSQL,
and React with no external message queue required.

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
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── auth.py              # POST /api/auth/signup, /api/auth/login
│   │       ├── admin.py             # /api/admin/* (CSV Import, Roles, LLM Config)
│   │       ├── teacher.py           # /api/teacher/* (Classes, Labs, Rules & Audit)
│   │       ├── student.py           # /api/student/* (Join via code, Sessions)
│   │       └── chat.py              # POST /api/chat/stream (SSE + DB Rate Check)
│   ├── .env                         # DATABASE_URL, JWT_SECRET (LLM configs moved to DB)
│   ├── requirements.txt
│   └── Dockerfile                   # Python 3.12-slim FastAPI build
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── App.jsx                  # React Router setup
│   │   ├── pages/                   # Login, Chat, Teacher, Admin
│   │   ├── components/              # UI Components, SessionSidebar, ProtectedRoute
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

### 1. SystemConfigs (NEW)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| key | String | Unique. e.g., `LLM_BASE_URL`, `LLM_MODEL` |
| value | String | The configuration value |
| updated_at | Timestamp | For tracking when admin changed configs |

### 2. Users
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Integer | Primary Key (can be String if using school ID) |
| username | String | Unique, Not Null |
| hashed_password | String | bcrypt. Not Null |
| role | Enum(student, teacher, admin) | Not Null |
| daily_token_quota | Integer | Hard limit (e.g., 50000). Set by Admin. |
| is_deleted | Boolean | Default: False — Soft delete flag |

### 3. Classes (NEW)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| name | String | Name of the class |
| teacher_id | FK | → Users.id (Must be a teacher) |
| invite_code | String | Unique, 6-character short code for zero-friction joining |
| is_deleted | Boolean | Default: False — Soft delete flag for historical retention |
| created_at | Timestamp | Timezone-aware (UTC) |

### 4. Class_Students (NEW)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| class_id | FK | → Classes.id |
| student_id | FK | → Users.id |
| joined_at | Timestamp | Timezone-aware (UTC) |
*Primary Key is composite: (class_id, student_id)*

### 5. Labs (NEW)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| class_id | FK | → Classes.id |
| name | String | E.g., "Python Basics Lab 1" |
| is_active | Boolean | Default: True. Set to False to lock lab (Read-only) |
| is_deleted | Boolean | Default: False — Soft delete flag |
| created_at | Timestamp | Timezone-aware (UTC) |

### 6. Rules (REFACTORED)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| level | Enum | `class`, `lab`, `student` |
| target_id | UUID / Int | The ID of the Class, Lab, or Student this applies to |
| rules_text | Text | The restrictive prompts/instructions designed by the teacher |
| is_active | Boolean | Default: True. Toggle for applying these rules |

### 7. Sessions (MODIFIED)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| user_id | FK | → Users.id |
| lab_id | FK | → Labs.id. Sessions are tightly scoped to a specific lab |
| is_deleted | Boolean | Default: False |
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

### A. Auth & Admin Operations

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/auth/signup` | — | Student self-registration (Default role: student). Fails if ID exists. |
| POST | `/api/auth/login` | — | Validate credentials. Return JWT containing ONLY `user_id`. |
| PUT | `/api/admin/users/{id}/role` | require_admin | Change user role (e.g., student <-> teacher). |
| POST | `/api/admin/users/import` | require_admin | CSV Bulk Import. Dry-run by default. `?force=true` overwrites passwords. |
| PUT | `/api/admin/llm/config` | require_admin | Zero-downtime update of `SystemConfigs` (Base URL, API Key, Model). |
| GET | `/api/admin/classes` | require_admin | God Mode: List all classes across the system. |
| GET | `/api/admin/classes/{class_id}/students` | require_admin | God Mode: View all students in a specific class. |
| GET | `/api/admin/labs` | require_admin | God Mode: List all labs across the system. |
| PUT | `/api/admin/classes/{class_id}/transfer` | require_admin | Force transfer class ownership (`teacher_id`). |
| GET | `/api/admin/analytics` | require_admin | Global platform-wide token usage dashboard. |
| DELETE | `/api/admin/sessions/{session_id}` | require_admin | Hard/Soft delete a specific session to purge inappropriate content. |
| POST | `/api/admin/maintenance/prune` | require_admin | Bulk Hard-Delete sessions/messages older than X days to free disk space. |

### B. Teacher Audit & Control

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | require_teacher | Create class and generate 6-char `invite_code`. |
| PUT | `/api/teacher/classes/{class_id}` | require_teacher | Rename/update class details. |
| DELETE | `/api/teacher/classes/{class_id}` | require_teacher | Soft delete a class. |
| POST | `/api/teacher/classes/{class_id}/reset-code` | require_teacher | Generate a new invite code (invalidating the old one). |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}`| require_teacher | Kick/Remove a student from the class. |
| POST | `/api/teacher/classes/{class_id}/labs`| require_teacher | Create a Lab under a Class. |
| PUT | `/api/teacher/labs/{lab_id}` | require_teacher | Rename lab or toggle `is_active` (Open/Close). |
| DELETE | `/api/teacher/labs/{lab_id}` | require_teacher | Soft delete a Lab. |
| PUT | `/api/teacher/rules` | require_teacher | Create/Update Rules (Class level, Lab level, or Student level). |
| GET | `/api/teacher/chat-history` | require_teacher | Fetch chat history. Filters: `?class_id=X&lab_id=Y&student_id=Z&session_id=W` |
| GET | `/api/teacher/analytics/classes/{class_id}` | require_teacher | Aggregated granular token usage for a specific class/lab. |

### C. Student Flow (Session & Chat)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/classes/join` | get_current_user | Submit 6-char `invite_code` to instantly join a Class. |
| DELETE | `/api/student/classes/{class_id}/leave` | get_current_user | Voluntarily leave a joined class. |
| POST | `/api/student/labs/{lab_id}/sessions` | get_current_user | Create a new Chat Session specifically within a Lab. |
| GET | `/api/student/sessions/{session_id}` | get_current_user | Fetch message history. **IDOR check required.** |
| PUT | `/api/student/sessions/{session_id}` | get_current_user | Rename a chat session (update title). |
| DELETE | `/api/student/sessions/{session_id}` | get_current_user | Soft delete a chat session from user history. |
| POST | `/api/chat/stream` | get_current_user | Core chat endpoint. Scoped by `session_id`. |

---

## 6. POST `/api/chat/stream` — Full Logic Spec (v5)

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

2. **Three-Tier Prompt Controller**
* Query `rules` table for active rules matching:
  - `level=class, target_id=class_id`
  - `level=lab, target_id=lab_id`
  - `level=student, target_id=current_user.id`
* Concatenate these rules sequentially (Class -> Lab -> Student) to build a robust, granular System Prompt.

3. **Dynamic LLM Proxy (Zero-Downtime)**
* Fetch active `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` from `SystemConfigs` table (cached in memory if needed).
* Proxy payload directly to the LLM engine using standard `AsyncOpenAI` client.

4. **SSE Streaming & Safe Async Write**
* Stream tokens via SSE (`text/event-stream`).
* Use `BackgroundTasks` to insert User message, LLM message (with tokens), and Upsert `usage_stats`.

---

## 7. Deployment & Run Instructions

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