<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Edu-LLM System Architecture</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #f8fafc;
        }
        .mermaid-container {
            display: flex;
            justify-content: center;
            background-color: white;
            padding: 2.5rem;
            border-radius: 1rem;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
            overflow-x: auto;
            border: 1px solid #e2e8f0;
        }
    </style>
</head>
<body class="min-h-screen p-6 md:p-10 text-slate-800">

    <div class="max-w-7xl mx-auto">
        <header class="mb-12 text-center">
            <div class="inline-block bg-indigo-100 text-indigo-800 text-xs font-bold px-4 py-1.5 rounded-full mb-4 uppercase tracking-widest shadow-sm">
                Contract-Driven Blueprint
            </div>
            <h1 class="text-4xl md:text-5xl font-extrabold tracking-tight text-slate-900 mb-4">
                Edu-LLM: Full RBAC Architecture
            </h1>
            <p class="text-lg text-slate-600 max-w-3xl mx-auto leading-relaxed">
                Visualizing the 3-Tier Role-Based Access Control, JWT Middleware routing, dynamic Poison Prompt injection, and async non-blocking data persistence.
            </p>
        </header>

        <main>
            <div class="mermaid-container mb-12">
                <div class="mermaid">
                    %%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#f8fafc', 'primaryEdgeColor': '#475569', 'lineColor': '#64748b', 'fontFamily': 'Inter'}}}%%
                    graph TD
                        %% Define visual classes
                        classDef default fill:#ffffff,stroke:#94a3b8,stroke-width:1px,color:#1e293b;
                        classDef dangerous fill:#fee2e2,stroke:#ef4444,stroke-width:2px,color:#991b1b,font-weight:bold;
                        classDef auth fill:#fef08a,stroke:#eab308,stroke-width:2px,color:#854d0e,font-weight:bold;
                        classDef streaming fill:#dbeafe,stroke:#3b82f6,stroke-width:2px,color:#1e40af,font-weight:bold;
                        classDef route fill:#f3e8ff,stroke:#d8b4fe,stroke-width:2px,color:#6b21a8;

                        subgraph Frontend ["1. Frontend Layer (React)"]
                            UI_S["👤 Student UI (/chat)<br>Pure Chat Window"]
                            UI_T["👨‍🏫 Teacher UI (/teacher)<br>Audit & Poison Setup"]
                            UI_A["🛠️ Admin UI (/admin)<br>Manage Users & Roles"]
                        end

                        subgraph Backend ["2. Backend Layer (FastAPI)"]
                            Auth["🔒 JWT Gateway<br>get_current_user() | require_teacher() | require_admin()"]
                            
                            Route_Admin["🛠️ Admin Routes<br>POST /api/admin/users"]
                            Route_Teacher["👨‍🏫 Teacher Routes<br>GET /api/teacher/sessions<br>PUT /api/teacher/poison"]
                            Route_Student["💬 Chat Route<br>POST /api/chat/stream"]

                            PromptCtrl["💉 Prompt Controller<br>Inject Poison Prompt"]
                            Proxy["🌊 Streaming Proxy<br>SSE Forwarding"]
                            AsyncDB["⚡ Background Tasks<br>Async Write"]
                        end

                        subgraph Database ["3. Database Layer (PostgreSQL)"]
                            DB_Users[("👥 Users Table<br>id, username, role")]
                            DB_Sessions[("📑 Sessions Table<br>id, user_id, poison_mode")]
                            DB_Messages[("💬 Messages Table<br>id, session_id, content")]
                        end

                        subgraph Compute ["4. Compute Layer (Ollama)"]
                            LLM["🧠 Qwen 35B MoE"]
                        end

                        %% Connections
                        UI_S == "JWT Token" ==> Auth
                        UI_T == "JWT Token" ==> Auth
                        UI_A == "JWT Token" ==> Auth

                        Auth -.->|"Validate Role"| DB_Users
                        Auth ==>|"Role: admin"| Route_Admin
                        Auth ==>|"Role: teacher"| Route_Teacher
                        Auth ==>|"Role: student"| Route_Student

                        Route_Admin -.->|"CRUD Users & Roles"| DB_Users
                        Route_Teacher -.->|"Read History"| DB_Messages
                        Route_Teacher -.->|"Set poison_mode"| DB_Sessions

                        Route_Student ==>|"1. Receive Prompt"| PromptCtrl
                        PromptCtrl -.->|"2. Check poison_mode"| DB_Sessions
                        PromptCtrl ==>|"3. Assembled Payload"| Proxy
                        
                        Proxy ==>|"4. POST /chat/completions"| LLM
                        LLM ==>|"5. Stream Tokens"| Proxy
                        Proxy ==>|"6. Server-Sent Events"| UI_S
                        
                        Proxy -.->|"7. Trigger Background Task"| AsyncDB
                        AsyncDB -.->|"8. Save Q&A"| DB_Messages

                        %% Apply Classes
                        class PromptCtrl dangerous;
                        class Auth auth;
                        class Proxy,LLM streaming;
                        class Route_Admin,Route_Teacher,Route_Student route;
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-yellow-200 transition hover:shadow-md">
                    <div class="flex items-center gap-3 mb-4">
                        <div class="bg-yellow-100 p-2 rounded-lg text-xl">🔒</div>
                        <h3 class="text-lg font-bold text-slate-900">3-Tier RBAC Gateway</h3>
                    </div>
                    <p class="text-sm text-slate-600 leading-relaxed">
                        FastAPI uses <code>Depends()</code> to enforce strict boundaries. <strong>Admins</strong> manage user roles. <strong>Teachers</strong> audit student history and toggle test conditions. <strong>Students</strong> are sandboxed exclusively to the chat interface.
                    </p>
                </div>
                
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-red-200 transition hover:shadow-md">
                    <div class="flex items-center gap-3 mb-4">
                        <div class="bg-red-100 p-2 rounded-lg text-xl">💉</div>
                        <h3 class="text-lg font-bold text-slate-900">Poison Controller</h3>
                    </div>
                    <p class="text-sm text-slate-600 leading-relaxed">
                        The core experimental feature. Before sending the student's code to Ollama, this checks the <code>Sessions</code> table. If <code>poison_mode > 0</code>, it stealthily injects rules (e.g., "no comments, bad variables") into the System Prompt.
                    </p>
                </div>
                
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-blue-200 transition hover:shadow-md">
                    <div class="flex items-center gap-3 mb-4">
                        <div class="bg-blue-100 p-2 rounded-lg text-xl">🌊</div>
                        <h3 class="text-lg font-bold text-slate-900">Async Streaming I/O</h3>
                    </div>
                    <p class="text-sm text-slate-600 leading-relaxed">
                        To guarantee a seamless "typewriter" effect, the proxy pipes tokens directly from the LLM to React via SSE. Database saving is handed off to a <code>BackgroundTask</code>, ensuring zero latency blockage.
                    </p>
                </div>
            </div>
        </main>
    </div>

    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10.9.6/dist/mermaid.esm.min.mjs';
        mermaid.initialize({ startOnLoad: true });
    </script>
</body>
</html>