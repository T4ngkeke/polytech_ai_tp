"""
test_agent_prompt.py — v7 system-prompt assembly.

Order matters for governance: persona (skill.md) first sets tone; retrieved
context is reference material; restrictive Class/Lab/Student rules come LAST so
constraints win and resist prompt-injection from retrieved/user text.
"""

from backend.app.agent.prompt import build_system_prompt


def test_persona_precedes_constraint_rules():
    prompt = build_system_prompt(
        skill_md="You are Prof. Ada, warm and Socratic.",
        class_rules="Never reveal full solutions.",
        lab_rules=None,
        student_rules=None,
        context_blocks=["Threads share the same address space."],
    )
    # persona before the constraint
    assert prompt.index("Prof. Ada") < prompt.index("Never reveal full solutions.")
    # retrieved context included, and placed before the constraints
    assert "Threads share the same address space." in prompt
    assert prompt.index("Threads share the same address space.") < prompt.index(
        "Never reveal full solutions."
    )


def test_low_evidence_adds_disclaimer_instruction():
    prompt = build_system_prompt(context_blocks=["thin context"], low_evidence=True)
    assert "[LOW EVIDENCE]" in prompt
    assert "not enough" in prompt.lower()


def test_no_disclaimer_when_evidence_is_fine():
    prompt = build_system_prompt(context_blocks=["solid context"], low_evidence=False)
    assert "[LOW EVIDENCE]" not in prompt
