"""
test_schema_v8.py — [v8.0] schema deltas over v7.3.

Pure column-presence guards (no DB round-trip needed) for the v8.0 additions:
Session dialogue-state + test-drive flag, the redefined RouterQueryLog v2 (every
router decision, not just low-confidence kNN fallbacks), and the AgentTraceLog
per-message observability table.
"""

from backend.app.models import AgentTraceLog, RouterQueryLog, Session


def _cols(model) -> set[str]:
    return {c.name for c in model.__table__.columns}


def test_session_has_dialogue_state_and_test_flag():
    cols = _cols(Session)
    assert "current_exercise_id" in cols  # sticky exercise fill
    assert "is_test" in cols              # teacher test-drive


def test_router_query_log_v2_columns():
    cols = _cols(RouterQueryLog)
    # v2 records every decision with full attribution.
    for c in ("route", "exercise_number", "number_source", "degraded",
              "is_test", "model_name", "prompt_version", "latency_ms",
              "lab_id", "created_at"):
        assert c in cols, f"RouterQueryLog missing v2 column {c}"


def test_router_query_log_drops_knn_telemetry_columns():
    cols = _cols(RouterQueryLog)
    # The kNN-era columns are gone (redefine, not extend).
    assert "chosen_route" not in cols
    assert "top_similarity" not in cols


def test_agent_trace_log_columns():
    cols = _cols(AgentTraceLog)
    for c in ("message_id", "session_id", "lab_id", "route", "node_latencies",
              "degraded_flags", "recall_count", "rerank_filtered_count",
              "all_filtered", "verdict", "retry_flipped", "red_flag", "created_at"):
        assert c in cols, f"AgentTraceLog missing column {c}"
