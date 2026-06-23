"""
test_agent_router.py — v7 agent intent router (rules-first classification).

Pure function, no LLM: routes a student message to one of
  agentic_search | rag | direct
"""

import pytest

from backend.app.agent.router import classify_intent


@pytest.mark.parametrize("message", [
    "How do I do exercise 2?",
    "I'm stuck on Exercise 3.1",
    "exercice 5 svp",
])
def test_exercise_reference_routes_to_agentic_search(message):
    assert classify_intent(message) == "agentic_search"


@pytest.mark.parametrize("message", [
    "What is multithreading?",
    "Explain pointers please",
    "qu'est-ce qu'un mutex",
])
def test_concept_question_routes_to_rag(message):
    assert classify_intent(message) == "rag"


@pytest.mark.parametrize("message", [
    "hello!",
    "thanks, that helped",
    "ok continue",
])
def test_other_messages_route_to_direct(message):
    assert classify_intent(message) == "direct"
