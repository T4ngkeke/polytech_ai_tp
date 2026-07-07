"""
test_answers_isolation.py — [v8.0 red line] the student path never touches Answers.

Answers live in the main DB (grilling decision B — no physical vault), so the ONLY
thing keeping them off the student-facing path is discipline: the chat / agent /
prompt / retrieval modules must never import the `Answer` model. This architecture
test guards that boundary — a future `from ...models import Answer` in any of them
fails here.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_GUARDED = [
    "backend/app/routers/chat.py",
    "backend/app/agent/graph.py",
    "backend/app/agent/router.py",
    "backend/app/agent/prompt.py",
    "backend/app/services/retrieval_service.py",
]


def _imports_answer(rel_path: str) -> bool:
    tree = ast.parse((_REPO_ROOT / rel_path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "models" in node.module:
            if any(alias.name == "Answer" for alias in node.names):
                return True
    return False


def test_student_path_never_imports_answer():
    offenders = [p for p in _GUARDED if _imports_answer(p)]
    assert not offenders, f"student-path modules import Answer (red line): {offenders}"
