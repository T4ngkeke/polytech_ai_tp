# Edu-LLM v7 — Agentic Upgrade (Design Summary)

> **Status:** Design / backlog. No code yet. Builds on v6 (`README.md`).
> **Core shift:** `/api/chat/stream` evolves from a **transparent LLM proxy** into an
> **Agent** that does intent routing, agentic search over structured data, RAG over
> concepts, and adaptive tutoring — plus an **off-peak document-ingestion
> pipeline** that never preempts live inference.
>
> Every building block is an established open-source pattern. The value is the
> **custom assembly for "education governance + constrained GPU"**.

---

## 1. Instructor Style: `skill.md`

- **How:** Reuse the existing polymorphic `Rules` table. `skill.md` = a `level=class`
  rule. In the Prompt Controller, place it **first** (sets tone); restrictive rules go
  after (constraints win, resisting prompt-injection bypass).
- **Principle:** Keep `skill.md` **short and resident** (a few hundred tokens — persona/style
  only). Long instructor material does **not** go here — it goes through Feature 2 (RAG,
  on-demand) following the *progressive disclosure* idea.

## 2. Document Ingestion + Hybrid Retrieval (core)

- **Two-way routing (= Adaptive-RAG pattern):**
  - **Exact mission** ("how to do exercise 2?") → **Agentic Search** over PostgreSQL
    structured tables.
  - **Concept question** ("what is multithreading?") → **RAG** over the vector store.
- **Vector store decision: use `pgvector`, not a third-party DB.** Keeps a single DB,
  lets structured + vector data JOIN (natural hybrid search). Migrate to Qdrant only at
  million-scale.
- **Data model sketch:**
  - `Documents` — state machine `pending → processing → indexed → failed`
  - `DocChunks` — `embedding vector(...)` + metadata
  - `Exercises` — LLM-extracted structured exercises (`number, statement, hints, solution`)
- **Three must-handle items:**
  1. **Filter retrieval by `lab_id` / `class_id`** — security boundary. No cross-class /
     cross-lab access, no leaking not-yet-open answers.
  2. **`solution` is an Agent-only hidden reference, never emitted** (see Feature 6 guardrails).
  3. **Re-indexing on doc update/delete** (invalidate old chunks + embeddings).
- **Citations:** retrieved results carry their source to encourage students to read originals.

## 3. Agent Orchestration: **LangGraph**

- **Decision:** Use LangGraph (not hand-rolled). Rationale: **2-day scrum / fast iteration** —
  a graph where "add a node = add business logic" stays maintainable and leaves room for
  future complexity.
- **Graph skeleton:**
  `Router (cheap classify) → [agentic_search | rag | direct] → Synthesize (skill.md + 3-tier rules) → SSE stream`
- **Router-first, avoid pure ReAct:** on slow bandwidth every extra ReAct step = one more
  full decode. Use rules/regex for obvious intents; reserve multi-hop ReAct as a "heavy
  weapon" for genuinely hard queries.
- **Starting point:** copy LangGraph's four official RAG recipes —
  **Adaptive RAG (≈ our router) / Agentic RAG / Corrective RAG (CRAG) / Self-RAG**.
- **TODO:** rework the current `AsyncOpenAI`-passthrough SSE layer to stream from graph
  `astream_events`.

## 4. Ingestion Scheduling: a docker-compose worker (No Airflow, No k8s)

- **Real need:** ingestion is **deferred**, GPU-aware, runs in **off-peak / no-class
  windows**, never disturbs live students.
- **Airflow rejected:** Airflow is heavy, is a time-based DAG scheduler, and **does not
  solve GPU contention** by itself.
- **No orchestration needed:** GPU arbitration happens **inside the inference engine**, not
  in the orchestration layer — so there is no inter-pod scheduling to manage. The only
  requirement is "a long-running process that polls a queue", which `restart: always`
  satisfies.
- **Adopted approach (stays in docker-compose):**
  - **Add one `worker` service** to the existing stack (postgres + backend + frontend +
    **worker**). It shares the backend's image / code / DB; only the entrypoint differs
    (a polling loop instead of uvicorn). No new tech — it reuses the same DB-as-queue
    pattern already used for rate limiting. It does not call other pods and does not split
    the web app.
  - **Worker logic:** poll an `IngestionJobs` table, concurrency = 1, submit extraction
    requests to vLLM/SGLang at **low priority**. The engine continuous-batches them
    alongside chat; chat always preempts (priority + preemption).
  - **"When to run" lives in the worker's Python**, not in any scheduler: at night /
    no-class windows pull freely; during the day pull only when `pynvml` reports GPU
    utilization below a threshold, else sleep. No external cron, no k8s CronJob.
- **k8s is a future option only:** needed only for multi-node / multi-GPU, horizontally
  scaling many workers, or complex DAG dependencies — none of which apply at 100 users on
  a single box. Revisit (CronJob / KEDA / Argo Workflows) only when that wall is hit.

## 5. Ingestion Model: **Reuse the loaded 120B, don't spin a second model**

- **Why:** 128 GB unified RAM already holds ~75 GB weights + ~30 GB KV cache — **no room
  for a separate 70B/30B**, and small models aren't trusted.
- **Conclusion:** ingestion reuses the running **SOTA 120B** (best quality, zero extra
  VRAM); vLLM batching maximizes throughput.
- **Extraction safety:** **guided / structured decoding** (JSON schema / Outlines /
  XGrammar) forces a valid schema — eliminates malformed/missing/hallucinated structure.

## 6. Adaptive Tutoring: **Prompt-Literacy Coaching** (simplified)

> **Design shift:** the heavy Learner-Model / Knowledge-Tracing (BKT/DKT) / LLM-as-judge /
> per-concept stack is **dropped**. The real teaching goal is **"how to ask GenAI"** — a
> learnable skill. We judge the *single most observable signal — how the student asks* — not
> their mastery, with **cheap, rules-first heuristics** (no LLM), mirroring the `router` and
> `GpuGate` patterns already in the codebase.

Two distinct layers:

- **(a) Effort / clarity — windowed, formative.** Per-message heuristic score (code-dump vs.
  explanation ratio; presence of "what I tried"; a concrete error message; an exercise
  reference). Fed into a **smoothed sliding window** (EMA) per *(student, lab)*. The trend
  sets a *coaching level* that adjusts the tutor's scaffolding. **Never gates, always
  answers.** When the window stays low, a **throttled** explicit tip teaches better
  questioning ("paste the exact error and what you tried"). Goal: **encourage thinking, not
  copy-paste.** A "stupid"/low-skill question is **never limited** — students have different
  levels.

- **(b) Lazy / answer-seeking — per-message guardrail.** Detects offloading-of-thinking
  ("just give me the answer to Q2", "solve this for me"). Triggers a **strict Socratic
  response**: guiding questions / hints only, **never the final answer**. "Limit" here means
  *don't hand over the answer* — it **still responds**, indirectly. Distinguished from a
  *specific question that shows thinking* ("I think the answer is X because Y, right?"), which
  is answered normally. The detector keys on **absence of the student's own attempt/reasoning**,
  not on merely mentioning an answer or exercise number.

- **Persistence:** lightweight `LearnerProfile` per *(student, lab)* — smoothed effort score,
  sample count, last-tip throttle marker. **Teacher-visible & teacher-overridable, transparent
  to the student.**
- **Governance red line (unchanged):** store **structured score + controlled-vocabulary
  strategy** only — **never insulting free text** ("this student is dumb" is a
  legal/discrimination hazard on an audited platform).
- **Answer guardrail (complements the data-layer rule):** `solution` already never enters the
  prompt (Feature 2); layer (b) now also **enforces indirect answering style**.

---

## Key Decisions

| Topic | Decision | Reason |
|---|---|---|
| Vector store | **pgvector** | Single DB, JOIN-able |
| skill.md | Reuse Rules table, short & resident | Fits 3-tier injection |
| Agent framework | **LangGraph** | Fast iteration, room to extend |
| Retrieval | Adaptive-RAG, router-first | Saves decode on slow bandwidth |
| Ingestion model | **Reuse 120B** | No RAM for a 2nd model + quality |
| Extraction safety | guided decoding | Eliminates structural errors |
| Scheduling | docker-compose `worker` + vLLM low-priority batch | Airflow rejected; k8s a future option only; GPU arbitration in engine |
| Student profile | **Prompt-literacy: rules-first effort window + lazy-question Socratic guardrail** | Simple, observable, teaches help-seeking; no taxonomy/LLM-judge needed |

## Open / pending (after hardware arrives)

1. **vLLM/SGLang priority scheduling** — decides whether "low-priority mixed batching"
   lands at zero cost.
2. **Context window:** enable **fp8 KV cache** to aim for 16k × 20 concurrent (under fp16,
   ~30 GB KV only covers ~8k × 20). Also keep actual usage under 8k via retrieval quality.
3. **Local MoE tool-calling stability** stress test (agentic search depends on it).
4. Final split of **real-time path vs background jobs** → deployment diagram.

---

## Open-source reference map (this is assembly, not invention)

- **Routing / agentic RAG:** LangGraph official recipes (Adaptive RAG, Agentic RAG, CRAG,
  Self-RAG); LlamaIndex `RouterQueryEngine`, `SubQuestionQueryEngine`, text-to-SQL + vector,
  Self-Query Retriever.
- **Structured extraction (LLM ETL):** Unstructured.io, Outlines / XGrammar / instructor.
- **RAG platforms (for inspiration only):** Onyx (ex-Danswer), RAGFlow, kotaemon, Quivr,
  Verba, Haystack, Dify.
- **Learner model:** Knowledge Tracing (BKT/DKT); Agent reflection (Generative Agents),
  LangMem / long-term memory.
- **Bespoke glue (no off-the-shelf):** 3-tier rule injection + skill.md governance;
  Socratic "never reveal solution" guardrail; GPU-aware off-peak ingestion on a single
  constrained box.
