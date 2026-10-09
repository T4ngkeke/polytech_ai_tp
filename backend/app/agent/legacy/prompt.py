"""Legacy chat compatibility module.

Used by the fallback in routers/chat.py, outside the M4 research loop.

prompt.py — system-prompt assembly for the synthesize node.

Assembly order (governance-critical):
  1. skill.md persona      — sets tone first
  2. retrieved context     — reference material (retrieved course-material chunks)
  3. Class → Lab → Student — restrictive rules LAST, so constraints win and
     resist prompt-injection from retrieved or user text.

Per-exercise guidance now lives in those class/lab rules: teachers write what
they want said about an exercise instead of the system storing exercise rows.
"""

_BASE = "You are a helpful AI tutor for an educational platform."


_ANSWER_GUARDRAIL = (
    "[ANSWER GUARDRAIL]\nThe student is asking for the answer directly. Do NOT give the "
    "final answer or full solution. Respond only with guiding questions and hints "
    "(Socratic), so the student reasons toward it themselves."
)


_LOW_EVIDENCE = (
    "[LOW EVIDENCE]\nThe reference material above may not fully cover this question. "
    "Answer from general knowledge but tell the student plainly that there was not enough "
    "course material on this, and suggest they double-check with the course documents."
)


_NO_MATERIAL = (
    "[NO COURSE MATERIAL]\nThe course materials do not cover this question. Tell the "
    "student plainly that the course documents don't cover this, then answer briefly "
    "from general knowledge if you can, and suggest they check with the instructor. "
    "Do NOT cite or invent any course material."
)


def build_no_material_prompt(
    *,
    skill_md: str | None = None,
    class_rules: str | None = None,
    lab_rules: str | None = None,
    student_rules: str | None = None,
    coaching_strategy: str | None = None,
    answer_seeking: bool = False,
) -> str:
    """[v8.0] Zero-context disclaimer branch: like direct, plus one honest sentence
    that the course material doesn't cover this. No reference material, no citations
    — never a confident answer with fake grounding. Rules still go LAST."""
    parts: list[str] = []
    if skill_md:
        parts.append(skill_md.strip())
    parts.append(_BASE)
    parts.append(_NO_MATERIAL)
    for label, text in (("CLASS", class_rules), ("LAB", lab_rules), ("STUDENT", student_rules)):
        if text and text.strip():
            parts.append(f"[{label} RULES]\n{text.strip()}")
    if coaching_strategy and coaching_strategy.strip():
        parts.append("[COACHING]\n" + coaching_strategy.strip())
    if answer_seeking:
        parts.append(_ANSWER_GUARDRAIL)
    return "\n\n".join(parts)


def build_system_prompt(
    *,
    skill_md: str | None = None,
    class_rules: str | None = None,
    lab_rules: str | None = None,
    student_rules: str | None = None,
    context_blocks: list[str] | None = None,
    coaching_strategy: str | None = None,
    answer_seeking: bool = False,
    low_evidence: bool = False,
) -> str:
    parts: list[str] = []

    if skill_md:
        parts.append(skill_md.strip())

    parts.append(_BASE)

    if context_blocks:
        joined = "\n\n".join(b.strip() for b in context_blocks if b and b.strip())
        if joined:
            parts.append(
                "[REFERENCE MATERIAL] (possibly relevant — ignore anything that "
                "isn't; cite only the material you actually use)\n" + joined
            )

    for label, text in (("CLASS", class_rules), ("LAB", lab_rules), ("STUDENT", student_rules)):
        if text and text.strip():
            parts.append(f"[{label} RULES]\n{text.strip()}")

    # Tutoring constraints go LAST so they win over everything above.
    if coaching_strategy and coaching_strategy.strip():
        parts.append("[COACHING]\n" + coaching_strategy.strip())
    if answer_seeking:
        parts.append(_ANSWER_GUARDRAIL)
    if low_evidence:
        parts.append(_LOW_EVIDENCE)

    return "\n\n".join(parts)
