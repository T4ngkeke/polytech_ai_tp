"""
graph.py — [v7] LangGraph chat agent.

Skeleton (router-first, per design):

    START → router → [agentic_search | rag | direct] → synthesize → END

The graph routes the message, retrieves lab-scoped context, and assembles the
final OpenAI messages payload (`messages_payload`). Token streaming to the LLM
happens in the chat endpoint, which consumes that payload.

`build_agent(db, embed_fn)` injects the DB session and a query embedder so the
graph is unit-testable with fakes.
"""

import uuid
from typing import Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.effort import assess_effort
from backend.app.agent.lazy import detect_answer_seeking
from backend.app.agent.prompt import build_system_prompt
from backend.app.agent.router import build_embedding_router
from backend.app.models import CoachingLevel
from backend.app.services import learner_service, router_service
from backend.app.services.retrieval_service import rag_search, search_exercises

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

# Default router kNN confidence threshold (overridden by ROUTER_KNN_THRESHOLD config).
DEFAULT_ROUTER_THRESHOLD = 0.35

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
    context_blocks: list[str]
    citations: list[dict]
    answer_seeking: bool
    coaching_strategy: str | None
    messages_payload: list[dict]


def build_agent(
    db: AsyncSession,
    embed_fn: EmbedFn,
    *,
    router_threshold: float = DEFAULT_ROUTER_THRESHOLD,
    embedding_model: str | None = None,
):
    """Compile the chat agent graph bound to a DB session + query embedder."""

    async def tutor_node(state: AgentState) -> dict:
        # Lazy/answer-seeking is per-message; effort feeds the smoothed window.
        answer_seeking = detect_answer_seeking(state["message"])
        coaching_strategy = None
        lab_id = state.get("lab_id")
        user_id = state.get("user_id")
        if lab_id and user_id:
            profile = await learner_service.record_effort(
                db, user_id=user_id, lab_id=lab_id, score=assess_effort(state["message"]),
            )
            coaching_strategy = _COACHING_STRATEGY.get(profile.coaching_level)
        return {"answer_seeking": answer_seeking, "coaching_strategy": coaching_strategy}

    async def router_node(state: AgentState) -> dict:
        # Embed the query once here and reuse it downstream (rag), to avoid a
        # second decode on a slow-bandwidth box.
        query_embedding = (await embed_fn([state["message"]]))[0]
        router = await build_embedding_router(
            embed_fn, router_threshold, embedding_model=embedding_model,
        )
        decision = router.route(state["message"], query_embedding)
        if decision.low_confidence:
            await router_service.log_low_confidence_query(
                db,
                message=state["message"],
                route=decision.route,
                top_similarity=decision.top_similarity,
                lab_id=state.get("lab_id"),
            )
        return {"route": decision.route, "query_embedding": query_embedding}

    async def agentic_search_node(state: AgentState) -> dict:
        lab_id = state.get("lab_id")
        if not lab_id:
            return {"context_blocks": []}
        hits = await search_exercises(db, lab_id)
        blocks = [
            f"{h.number}: {h.statement}" + (f"\nHint: {h.hints}" if h.hints else "")
            for h in hits
        ]
        return {"context_blocks": blocks}

    async def rag_node(state: AgentState) -> dict:
        lab_id = state.get("lab_id")
        if not lab_id:
            return {"context_blocks": []}
        # Reuse the embedding computed in the router; fall back if missing.
        embedding = state.get("query_embedding") or (await embed_fn([state["message"]]))[0]
        hits = await rag_search(db, embedding, lab_id, k=4)
        return {
            "context_blocks": [h.content for h in hits],
            "citations": [
                {"document_id": str(h.document_id), "page_no": h.page_no} for h in hits
            ],
        }

    async def direct_node(state: AgentState) -> dict:
        return {"context_blocks": []}

    async def synthesize_node(state: AgentState) -> dict:
        system = build_system_prompt(
            skill_md=state.get("skill_md"),
            class_rules=state.get("class_rules"),
            lab_rules=state.get("lab_rules"),
            student_rules=state.get("student_rules"),
            context_blocks=state.get("context_blocks"),
            coaching_strategy=state.get("coaching_strategy"),
            answer_seeking=state.get("answer_seeking", False),
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
    graph.add_node("agentic_search", agentic_search_node)
    graph.add_node("rag", rag_node)
    graph.add_node("direct", direct_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "tutor")
    graph.add_edge("tutor", "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["route"],
        {"agentic_search": "agentic_search", "rag": "rag", "direct": "direct"},
    )
    graph.add_edge("agentic_search", "synthesize")
    graph.add_edge("rag", "synthesize")
    graph.add_edge("direct", "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile()
