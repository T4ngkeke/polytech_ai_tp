"""
services/rule_check.py — does a teacher's rule list contradict itself?

When two rules of one list contradict, the tutor follows one of them without
saying so, and not the same one in every mode (rule test, 2026-11-16). Rules
of different levels do not need this check: the prompt says which level wins
(rule_service.RULES_PRECEDENCE). Rules are free text, so a model reads them.

The teacher page asks after a change to a list; the teacher reviews the rules
or ignores the warning. A warning, never a block: the model can be wrong.
"""

from __future__ import annotations

import json
import re

CHECK_PROMPT = '''You check the rules a teacher gives an AI tutor for students. The rules are numbered.
Two rules contradict when one answer cannot follow both: for example "Always answer in French" and
"Always answer in English", or "Never give the full solution" and "Always give the complete code".
Rules that only add to each other do not contradict: "Keep answers short" and "End with a question"
can both be followed. A rule with an exception ("short, unless the student asks for more") contradicts
only what its exception does not allow.
Report only clear contradictions, not style preferences you would weigh differently.
Reply with JSON only: {"conflicts": [{"rules": [2, 5], "reason": "one short sentence for the teacher"}]}
and {"conflicts": []} when the rules can all be followed together.'''

MAX_REASON = 300
_JSON = re.compile(r'\{.*\}', re.S)


def numbered_rules(rules: dict[int, str]) -> str:
    return '\n'.join(f'{n}. {text.strip()}' for n, text in sorted(rules.items()))


def parse_conflicts(reply: str, numbers: set[int]) -> list[dict]:
    """The pairs of rule numbers and their reasons, from the model's reply.
    Anything malformed is dropped: no pair is better than a wrong one."""
    match = _JSON.search(reply or '')
    if not match:
        raise ValueError('rule_check_bad_reply')
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        raise ValueError('rule_check_bad_reply') from None
    found, seen = [], set()
    for item in payload.get('conflicts') or [] if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        rules = item.get('rules')
        if (not isinstance(rules, list) or len(rules) != 2
                or not all(isinstance(n, int) and n in numbers for n in rules) or rules[0] == rules[1]):
            continue
        pair = tuple(sorted(rules))
        if pair in seen:
            continue
        seen.add(pair)
        found.append({'rules': list(pair), 'reason': str(item.get('reason') or '').strip()[:MAX_REASON]})
    return found


async def find_conflicts(model, rules: dict[int, str] | list[str], *, timeout: float = 60.0) -> list[dict]:
    """[{'rules': [i, j], 'reason': str}]. rules: {number shown to the teacher:
    text} (a list is numbered from 1), so the reason names the same numbers as
    the page. Fewer than two rules: no model call."""
    if isinstance(rules, list):
        rules = {n: text for n, text in enumerate(rules, 1)}
    if len(rules) < 2:
        return []
    reply = await model([{'role': 'system', 'content': CHECK_PROMPT},
                         {'role': 'user', 'content': 'Rules:\n' + numbered_rules(rules)}],
                        600, timeout, purpose='rule_check')
    return parse_conflicts(reply.content, set(rules))
