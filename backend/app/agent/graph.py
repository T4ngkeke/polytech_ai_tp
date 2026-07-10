"""
graph.py — [v8.0] LangGraph chat agent.

    START → router → ┬ exercise → tutor → synthesize → END
                     ├ rag              → synthesize → END
                     ├ direct           → synthesize → END
                     └ clarify                       → END

One router LLM call classifies the message and (exercise-only) also assesses the
effort/answer-seeking coaching signals; `tutor` runs on the exercise leg only,
folding effort into the smoothed per-(student, lab) window. The graph retrieves
lab-scoped context and assembles the final OpenAI messages payload
(`messages_payload`); token streaming to the LLM happens in the chat endpoint.

`build_agent(db, embed_fn, router_llm_fn, ...)` injects the DB session, a query
embedder, and the router LLM so the graph is unit-testable with fakes.
"""

import uuid
from typing import Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.prompt import (
    build_clarify_prompt,
    build_no_material_prompt,
    build_system_prompt,
)
from backend.app.agent.router import (
    ROUTER_PROMPT_VERSION,
    RouterLLMFn,
    classify,
    resolve_number,
)
from backend.app.agent.selfeval import GradeFn, RewriteFn, run_self_eval_loop
from backend.app.models import Audience, CoachingLevel
from backend.app.services import learner_service, router_service
from backend.app.services.retrieval_service import (
    RerankFn,
    hybrid_search,
    list_exercise_numbers,
    search_exercises,
)
from backend.app.services.trace_service import TraceBuilder

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


async def _identity_rewrite(query: str) -> str:
    """Fallback rewrite when none is configured: re-retrieve the same query."""
    return query

# [v8.0] The router's effort enum → the float the EMA window expects. Aligned to
# learner_service watermarks (LOW 0.35 / HIGH 0.60): sustained low/high cross them,
# mid sits in the hysteresis band. Missing field → neutral mid.
_EFFORT_SCORE = {"low": 0.2, "mid": 0.475, "high": 0.8}

# Neutral, controlled-vocabulary strategies per coaching level (never insulting).
_COACHING_STRATEGY = {
    CoachingLevel.low: (
        "The student's recent questions are low-context. Before guiding, ask what they "
        "have already tried and the exact error/output. Be extra Socratic and encourage "
        "them to think step by step rather than copy-paste."
    ),
    CoachingLevel.high: (
        "The student asks well-structured questions. Engage directly and deeply at the "
        "step where they are stuck."
    ),
}


class AgentState(TypedDict, total=False):
    message: str
    class_id: uuid.UUID | None
    lab_id: uuid.UUID | None
    user_id: uuid.UUID
    history: list[dict]
    skill_md: str | None
    class_rules: str | None
    lab_rules: str | None
    student_rules: str | None
    route: str
    query_embedding: list[float]
    exercise_number: int | None
    number_source: str
    search_terms: list[str]
    effort: str | None
    sticky_number: int | None
    sticky_excerpt: str | None
    exercise_numbers: list[str]
    prev_was_clarify: bool
    is_test: bool
    context_blocks: list[str]
    citations: list[dict]
    low_evidence: bool
    no_material: bool
    answer_seeking: bool
    coaching_strategy: str | None
    messages_payload: list[dict]


def build_agent(
    db: AsyncSession,
    embed_fn: EmbedFn,
    router_llm_fn: RouterLLMFn,
    *,
    rerank_fn: RerankFn | None = None,
    grade_fn: GradeFn | None = None,
    rewrite_fn: RewriteFn | None = None,
    max_retries: int = 1,
    router_model_name: str = "",
    rerank_score_threshold: float | None = None,
    trace: TraceBuilder | None = None,
):
    """Compile the chat agent graph bound to a DB session, a query embedder, and
    the one-call router LLM (`router_llm_fn`)."""

    async def tutor_node(state: AgentState) -> dict:
        # [v8.0] Exercise-only coaching, driven by the router's effort signal (the
        # regex heuristics are gone). `answer_seeking` is already in state from the
        # router (set only on the exercise leg). This node folds effort into the
        # smoothed per-(student, lab) window and derives the coaching strategy.
        coaching_strategy = None
        lab_id = state.get("lab_id")
        user_id = state.get("user_id")
        # [v8.0 §11A] A teacher test-drive never updates student coaching data.
        if lab_id and user_id and not state.get("is_test", False):
            score = _EFFORT_SCORE.get(state.get("effort"), 0.475)  # missing → neutral mid
            profile = await learner_service.record_effort(
                db, user_id=user_id, lab_id=lab_id, score=score,
            )
            coaching_strategy = _COACHING_STRATEGY.get(profile.coaching_level)
        return {"coaching_strategy": coaching_strategy}

    async def router_node(state: AgentState) -> dict:
        # [v8.0] One LLM call classifies the message; resolve_number arbitrates
        # the number's origin (three-tier: explicit > context > sticky > none);
        # every decision is logged. No embedding here — only rag needs one, and
        # rag embeds its own query, so direct/exercise pay nothing.
        history = state.get("history", [])
        recent_turns = history[-2:] if history else []
        sticky_number = state.get("sticky_number")
        sticky_excerpt = state.get("sticky_excerpt")

        decision = await classify(
            state["message"],
            recent_turns=recent_turns,
            sticky_number=sticky_number,
            sticky_excerpt=sticky_excerpt,
            llm_fn=router_llm_fn,
        )
        number, source = resolve_number(
            decision.exercise_number, decision.number_source,
            sticky_number, decision.sticky_matches,
        )

        # DB validation + clarify decision — only meaningful with a lab. The three
        # clarify triggers (explicit/context number not in DB, no number + sticky
        # mismatch, no number + no sticky) all collapse to "no resolvable existing
        # number". Anti-loop: a second consecutive unresolved turn falls through to
        # rag instead of clarifying again.
        route = decision.route
        exercise_numbers: list[str] = []
        if route == "exercise" and state.get("lab_id"):
            pairs = await list_exercise_numbers(
                db, state["lab_id"], audience=Audience.student
            )
            valid = {n for _, n in pairs if n is not None}
            if number is None or number not in valid:
                if state.get("prev_was_clarify"):
                    route = "rag"
                else:
                    route = "clarify"
                    exercise_numbers = [raw for raw, _ in pairs]

        await router_service.log_decision(
            db,
            message=state["message"],
            route=route,
            exercise_number=number,
            number_source=source,
            degraded=decision.degraded,
            latency_ms=decision.latency_ms,
            model_name=router_model_name or None,
            prompt_version=ROUTER_PROMPT_VERSION,
            lab_id=state.get("lab_id"),
            is_test=state.get("is_test", False),
        )
        if trace is not None:
            trace.set("route", route)
            if decision.degraded:
                trace.flag("router")
        result = {
            "route": route,
            "exercise_number": number,
            "number_source": source,
            "exercise_numbers": exercise_numbers,
            "search_terms": decision.search_terms,
        }
        # Coaching signals are exercise-only. Gate on the FINAL route so an
        # anti-loop exercise→rag re-route never leaks the guardrail onto a concept
        # answer. tutor (exercise leg only) consumes `effort`; synthesize reads
        # `answer_seeking` — absent elsewhere → no guardrail.
        if route == "exercise":
            result["effort"] = decision.effort
            result["answer_seeking"] = decision.answer_seeking
        return result

    async def exercise_node(state: AgentState) -> dict:
        lab_id = state.get("lab_id")
        if not lab_id:
            return {"context_blocks": []}
        hits = await search_exercises(
            db, lab_id, number=state.get("exercise_number"), audience=Audience.student,
            # Test-drive sessions preview pending_review drafts; students never do.
            include_draft_hints=state.get("is_test", False),
        )
        blocks = [
            f"{h.number}: {h.statement}"
            + ("\nHints:\n" + "\n".join(f"- {t}" for t in h.hints) if h.hints else "")
            for h in hits
        ]

        # [v8.0] Supplement with related course material (CM). Retrieval query =
        # the statement (+ approved hints); on a follow-up (sticky fill) the current
        # keywords ride along so the focus moves with the question ("why threads?"
        # pulls the threads slides). One fixed lookup, no loop. CM used → cite.
        citations: list[dict] = []
        if hits:
            statement = hits[0].statement
            terms = state.get("search_terms") or []
            if state.get("number_source") == "sticky" and terms:
                query = statement + " " + " ".join(terms)
            else:
                query = statement
            embedding = (await embed_fn([query]))[0]
            cm_hits, _all_filtered = await hybrid_search(
                db,
                query_text=query,
                query_embedding=embedding,
                lab_id=lab_id,
                audience=Audience.student,
                rerank_fn=rerank_fn,
                top_k=3,
                class_id=state.get("class_id"),
            )
            for h in cm_hits:
                blocks.append(h.content)
                citations.append({"document_id": str(h.document_id), "page_no": h.page_no})

        return {"context_blocks": blocks, "citations": citations}

    async def rag_node(state: AgentState) -> dict:
        lab_id = state.get("lab_id")
        if not lab_id:
            return {"context_blocks": []}
        message = state["message"]
        base_embedding = state.get("query_embedding")
        all_filtered = False

        async def retrieve_fn(query: str) -> list:
            nonlocal all_filtered
            # Reuse the router's embedding for the original query; embed rewrites.
            # [v8.0] Embedding endpoint down → BM25-only (embedding None) + flag, never
            # a 500. Only the main LLM and main DB are hard dependencies.
            try:
                if query == message and base_embedding:
                    embedding = base_embedding
                else:
                    embedding = (await embed_fn([query]))[0]
            except Exception:
                embedding = None
                if trace is not None:
                    trace.flag("embedding")
            # Hybrid recall (vector + BM25) → RRF → rerank, student-audience scoped in SQL.
            # [v8.0] all_filtered (the rerank threshold dropped everything) drives the
            # zero-context disclaimer branch below; the gate ships disabled (threshold
            # None) until calibrated, so this stays dormant in production until then.
            hits, all_filtered = await hybrid_search(
                db,
                query_text=query,
                query_embedding=embedding,
                lab_id=lab_id,
                audience=Audience.student,
                rerank_fn=rerank_fn,
                top_k=4,
                score_threshold=rerank_score_threshold,
                class_id=state.get("class_id"),
            )
            return hits

        low_evidence = False
        if grade_fn is None:
            hits = await retrieve_fn(message)
        else:
            outcome = await run_self_eval_loop(
                message,
                retrieve_fn=retrieve_fn,
                grade_fn=grade_fn,
                rewrite_fn=rewrite_fn or _identity_rewrite,
                max_retries=max_retries,
            )
            hits = outcome.hits
            low_evidence = outcome.disclaimer

        # [v8.0] Zero-context disclaimer: the threshold dropped everything → inject
        # nothing, cite nothing, state honestly that the material doesn't cover this.
        if all_filtered and not hits:
            return {"context_blocks": [], "citations": [], "no_material": True}

        return {
            "context_blocks": [h.content for h in hits],
            "citations": [
                {"document_id": str(h.document_id), "page_no": h.page_no} for h in hits
            ],
            "low_evidence": low_evidence,
        }

    async def direct_node(state: AgentState) -> dict:
        return {"context_blocks": []}

    async def clarify_node(state: AgentState) -> dict:
        # Lightweight: no retrieval — the model just asks which exercise, offering
        # the lab's number list, in the skill.md tutoring voice.
        system = build_clarify_prompt(
            skill_md=state.get("skill_md"),
            class_rules=state.get("class_rules"),
            lab_rules=state.get("lab_rules"),
            student_rules=state.get("student_rules"),
            exercise_numbers=state.get("exercise_numbers"),
        )
        payload = (
            [{"role": "system", "content": system}]
            + list(state.get("history", []))
            + [{"role": "user", "content": state["message"]}]
        )
        return {"messages_payload": payload}

    async def synthesize_node(state: AgentState) -> dict:
        if state.get("no_material"):
            # Zero-context disclaimer branch: no reference material, no citations.
            system = build_no_material_prompt(
                skill_md=state.get("skill_md"),
                class_rules=state.get("class_rules"),
                lab_rules=state.get("lab_rules"),
                student_rules=state.get("student_rules"),
                coaching_strategy=state.get("coaching_strategy"),
                answer_seeking=state.get("answer_seeking", False),
            )
        else:
            system = build_system_prompt(
                skill_md=state.get("skill_md"),
                class_rules=state.get("class_rules"),
                lab_rules=state.get("lab_rules"),
                student_rules=state.get("student_rules"),
                context_blocks=state.get("context_blocks"),
                coaching_strategy=state.get("coaching_strategy"),
                answer_seeking=state.get("answer_seeking", False),
                low_evidence=state.get("low_evidence", False),
            )
        payload = (
            [{"role": "system", "content": system}]
            + list(state.get("history", []))
            + [{"role": "user", "content": state["message"]}]
        )
        return {"messages_payload": payload}

    graph = StateGraph(AgentState)
    graph.add_node("tutor", tutor_node)
    graph.add_node("router", router_node)
    graph.add_node("exercise", exercise_node)
    graph.add_node("rag", rag_node)
    graph.add_node("direct", direct_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["route"],
        {"exercise": "exercise", "rag": "rag", "direct": "direct", "clarify": "clarify"},
    )
    # [v8.0] tutor (effort/coaching + guardrail) runs ONLY on the exercise leg.
    graph.add_edge("exercise", "tutor")
    graph.add_edge("tutor", "synthesize")
    graph.add_edge("rag", "synthesize")
    graph.add_edge("direct", "synthesize")
    # clarify builds its own payload (no synthesize/retrieval) and ends.
    graph.add_edge("clarify", END)
    graph.add_edge("synthesize", END)

    return graph.compile()
