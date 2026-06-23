"""
test_agent_lazy.py — v7 lazy / answer-seeking guardrail.

Detects offloading-of-thinking ("just give me the answer") so the tutor can
respond Socratically. Crucially must NOT flag a specific question that shows the
student's own attempt/reasoning.
"""

import pytest

from backend.app.agent.lazy import detect_answer_seeking


@pytest.mark.parametrize("message", [
    "Give me the answer to question 2.",
    "just give me the solution",
    "solve this for me",
    "do exercise 2 for me",
    "what is the answer to exercise 3?",
    "donne-moi la réponse de l'exercice 2",
])
def test_answer_demands_are_flagged(message):
    assert detect_answer_seeking(message) is True


@pytest.mark.parametrize("message", [
    # shows reasoning / an attempt → NOT lazy
    "I think the answer to Q2 is 42 because I summed the list, is that right?",
    "I'm stuck on exercise 2: I tried a for-loop but got an IndexError.",
    "Can you explain how to approach exercise 2?",
    "What is a mutex?",
])
def test_thinking_questions_are_not_flagged(message):
    assert detect_answer_seeking(message) is False
