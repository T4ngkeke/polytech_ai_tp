"""
services/teaching_reports.py — a model writes a short report for the teacher.

Two kinds, both for one lab, made when the teacher asks:
- the lab: what students asked, which PDF pages, what was hard, how to
  prepare the next session;
- one student: how they ask, topics, what seems hard or understood.

The numbers are counted here, never by the model. The model gets them with the
questions themselves (students named S1, S2, ...), not the answers. A question
is tied to PDF pages by the citations of its answer; a question without
citations (Direct mode, exam mode) is tied to the pages a course search finds
for it (top 3 passages, no answer model). Teacher test sessions are left out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from backend.app.models import (
    AgentRun, AuditReport, Document, Lab, Message, MessageCitation, MessageFeedback,
    SenderType, Session, User,
)
from backend.app.services.retrieval_service import detect_language, hybrid_search

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 150          # the most recent questions go to the model
QUESTION_CHARS = 300
MATCHED_PASSAGES = 3
TOP_PAGES = 12
MAX_OUTPUT = 1500
CALL_SECONDS = 180
STALE_AFTER = timedelta(minutes=10)
PROBLEM_STOPS = {"budget_exhausted": "tool limit reached", "no_answer": "no answer"}

_tasks: set[asyncio.Task] = set()     # keeps running reports from being collected


class NoQuestions(Exception):
    pass


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def _lab_documents(db, lab: Lab) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """Published documents students of the lab can read: id -> (revision, filename)."""
    rows = (await db.execute(select(Document.id, Document.current_revision_id, Document.filename).where(
        Document.class_id == lab.class_id, Document.lifecycle == "active",
        Document.current_revision_id.is_not(None),
        or_(Document.lab_id == lab.id, Document.lab_id.is_(None))))).all()
    return {doc_id: (rev_id, name) for doc_id, rev_id, name in rows}


async def gather_facts(db, lab: Lab, *, student_id: uuid.UUID | None = None, embed_fn=None) -> dict:
    """Everything the model is told, counted from stored rows."""
    rows = (await db.execute(
        select(Message.id, Message.session_id, Message.sender, Message.content, Message.created_at,
               Message.answer_mode, Message.feedback, Session.user_id)
        .join(Session, Session.id == Message.session_id)
        .where(Session.lab_id == lab.id, Session.is_test.is_(False),
               *([Session.user_id == student_id] if student_id else []))
        .order_by(Message.session_id, Message.created_at))).all()

    # Pair each question with the answer that follows it in its conversation.
    questions, pending = [], {}
    for r in rows:
        if r.sender == SenderType.user:
            pending[r.session_id] = {"id": r.id, "user_id": r.user_id, "text": r.content,
                                     "at": r.created_at, "mode": None, "down": False, "answer_id": None}
            questions.append(pending[r.session_id])
        elif r.sender == SenderType.llm and r.session_id in pending:
            q = pending.pop(r.session_id)
            q.update(mode=r.answer_mode, answer_id=r.id,
                     down=getattr(r.feedback, "value", r.feedback) == MessageFeedback.down.value)
    if not questions:
        raise NoQuestions()
    questions.sort(key=lambda q: q["at"])

    documents = await _lab_documents(db, lab)
    names = {doc_id: name for doc_id, (_, name) in documents.items()}
    pages_of: dict[uuid.UUID, set[tuple[str, int]]] = defaultdict(set)
    answer_ids = [q["answer_id"] for q in questions if q["answer_id"]]
    if answer_ids:
        cited = (await db.execute(select(MessageCitation.message_id, MessageCitation.document_id,
                                         MessageCitation.pdf_page_no)
                                  .where(MessageCitation.message_id.in_(answer_ids)))).all()
        missing = {d for _, d, _ in cited if d not in names}
        if missing:
            names.update(dict((await db.execute(select(Document.id, Document.filename)
                                                .where(Document.id.in_(missing)))).all()))
        by_answer = defaultdict(set)
        for message_id, doc_id, page in cited:
            by_answer[message_id].add((names.get(doc_id, "deleted file"), page))
        for q in questions:
            if q["answer_id"] in by_answer:
                pages_of[q["id"]] = by_answer[q["answer_id"]]
                q["pages_from"] = "cited"

    # Questions without citations: the pages a course search finds for them.
    unmatched = [q for q in questions[-MAX_QUESTIONS:] if q["id"] not in pages_of]
    if unmatched and documents and embed_fn is not None:
        snapshot = {doc_id: rev for doc_id, (rev, _) in documents.items()}
        try:
            vectors = await embed_fn([q["text"][:QUESTION_CHARS * 3] for q in unmatched])
        except Exception as exc:       # the report is still useful without it
            logger.warning("Report: embedding failed (%s); questions stay unmatched", type(exc).__name__)
            vectors = []
        for q, vector in zip(unmatched, vectors):
            hits, _ = await hybrid_search(db, q["text"], vector, lab.id, class_id=lab.class_id,
                                          snapshot_revisions=snapshot, top_k=MATCHED_PASSAGES,
                                          language="auto")
            pages_of[q["id"]] = {(names.get(h.document_id, "?"), h.page_no) for h in hits if h.page_no}
            q["pages_from"] = "search"

    file_questions, page_questions = Counter(), Counter()
    for q in questions:
        pages = pages_of.get(q["id"], set())
        for name in {n for n, _ in pages}:
            file_questions[name] += 1
        for page in pages:
            page_questions[page] += 1

    runs = (await db.execute(select(AgentRun.user_message_id, AgentRun.status, AgentRun.stop_reason)
                             .join(Session, Session.id == AgentRun.session_id)
                             .where(Session.lab_id == lab.id, Session.is_test.is_(False),
                                    *([Session.user_id == student_id] if student_id else [])))).all()
    problem = {m: ("failed" if s == "failed" else PROBLEM_STOPS[r]) for m, s, r in runs
               if m and (s == "failed" or r in PROBLEM_STOPS)}

    # Students become S1, S2, ... in order of their first question.
    alias = {}
    for q in questions:
        alias.setdefault(q["user_id"], f"S{len(alias) + 1}")
    per_student = Counter(q["user_id"] for q in questions)
    recent = questions[-MAX_QUESTIONS:]
    facts = {
        "lab": lab.name,
        "period": {"first_question": _iso(questions[0]["at"]), "last_question": _iso(questions[-1]["at"])},
        "questions": len(questions),
        "questions_given_to_model": len(recent),
        "students": len(per_student),
        "questions_per_student": sorted(per_student.values(), reverse=True),
        "modes": dict(Counter(q["mode"] or "unknown" for q in questions)),
        "thumbs_down": sum(q["down"] for q in questions),
        "answers_not_finished": len([q for q in questions if q["id"] in problem]),
        "documents_in_lab": sorted(name for _, name in documents.values()),
        "documents_by_questions": [{"file": f, "questions": n} for f, n in file_questions.most_common()],
        "pages_by_questions": [{"file": f, "page": p, "questions": n}
                               for (f, p), n in page_questions.most_common(TOP_PAGES)],
        "pages_matched_by_search": sum(q.get("pages_from") == "search" for q in questions),
        "language": "French" if detect_language(q["text"] for q in questions) == "fr" else "English",
        "question_list": [{
            "student": alias[q["user_id"]], "date": q["at"].date().isoformat(), "mode": q["mode"],
            "text": " ".join(q["text"].split())[:QUESTION_CHARS],
            "pages": sorted(f"{f} p.{p}" for f, p in pages_of.get(q["id"], set()))[:3],
            **({"thumbs_down": True} if q["down"] else {}),
            **({"problem": problem[q["id"]]} if q["id"] in problem else {}),
        } for q in recent],
    }
    return facts


LAB_PROMPT = """You write a short report for a teacher about how students used the course assistant in one lab ({lab}).
Use only the facts and the student questions below. Never invent numbers: take them from the facts.
Students are named S1, S2, ...; keep those names. Write in {language}, in Markdown, at most 450 words,
with exactly these sections, headings as written here:
## {h[0]}
Two or three sentences with the main numbers.
## {h[1]}
The main topics, grouped, most frequent first, each with an approximate number of questions and one short quoted example.
## {h[2]}
Which PDFs and pages the questions concern most, and documents no question concerns. Say that pages
come from the answers' citations, or from a course search when an answer cited nothing.
## {h[3]}
Recurring misunderstandings, vague or off-topic questions, and questions the assistant answered badly
(thumbs down, answers not finished).
## {h[4]}
Three to five concrete suggestions for the teacher (what to explain again, which page to show, which exercise to give)."""

STUDENT_PROMPT = """You write a short report for a teacher about one student ({student}) in one lab ({lab}), from the
questions this student asked the course assistant. Use only the facts and questions below; never invent
numbers. Describe what the questions show; do not grade the student. When there are few questions, say
that the evidence is thin. Write in {language}, in Markdown, at most 350 words, with exactly these sections,
headings as written here:
## {h[0]}
## {h[1]}
Precise or vague, asks for explanations or for solutions, sends code, language and tone.
## {h[2]}
## {h[3]}
## {h[4]}
Two or three concrete ideas."""

# The headings in the report's language: the model copies headings as given.
HEADINGS = {
    "lab": {"English": ["Summary", "What students asked about", "Course documents and pages", "Difficulties",
                        "How to prepare the next session"],
            "French": ["Résumé", "Ce que les étudiants ont demandé", "Documents et pages du cours", "Difficultés",
                       "Préparer la prochaine séance"]},
    "student": {"English": ["Summary", "How the student asks", "Topics",
                            "What seems understood and what seems difficult", "Suggestions for the teacher"],
                "French": ["Résumé", "Comment l'étudiant pose ses questions", "Sujets",
                           "Ce qui semble acquis et ce qui semble difficile", "Pistes pour l'enseignant"]},
}


def build_messages(facts: dict, *, student: str | None = None) -> list[dict]:
    listed = facts["question_list"]
    shown = {k: v for k, v in facts.items() if k not in ("question_list", "language")}
    language = facts["language"]
    headings = HEADINGS["student" if student else "lab"].get(language, HEADINGS["lab"]["English"])
    system = (STUDENT_PROMPT.format(student=student, lab=facts["lab"], language=language, h=headings)
              if student else LAB_PROMPT.format(lab=facts["lab"], language=language, h=headings))
    lines = [f'- [{q["student"]}, {q["date"]}, {q["mode"] or "?"}] {q["text"]}'
             + (f' (pages: {", ".join(q["pages"])})' if q["pages"] else "")
             + (" [thumbs down]" if q.get("thumbs_down") else "")
             + (f' [{q["problem"]}]' if q.get("problem") else "") for q in listed]
    user = ("Facts:\n" + json.dumps(shown, ensure_ascii=False, indent=1)
            + f"\n\nQuestions ({len(listed)} most recent of {facts['questions']}):\n" + "\n".join(lines))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def write_report(factory, report_id: uuid.UUID, model, *, model_name: str = "",
                       embed_fn=None) -> None:
    """Fill a running report: facts, then the model's text. Never raises."""
    facts = None
    try:
        async with factory() as db:
            report = await db.get(AuditReport, report_id)
            lab = await db.get(Lab, report.lab_id)
            student = await db.get(User, report.student_id) if report.student_id else None
            facts = await gather_facts(db, lab, student_id=report.student_id, embed_fn=embed_fn)
        reply = await model(build_messages(facts, student=student and "S1"), MAX_OUTPUT, CALL_SECONDS,
                            purpose="teacher_report")
        content, status, error = (reply.content or "").strip(), "done", None
        if not content:
            status, error = "failed", "empty_reply"
    except NoQuestions:
        content, status, error = None, "failed", "no_questions"
    except Exception as exc:
        logger.exception("Teacher report failed")
        content, status, error = None, "failed", type(exc).__name__
    async with factory() as db:
        report = await db.get(AuditReport, report_id)
        report.status, report.content, report.facts, report.error = status, content, facts, error
        report.model = model_name or None
        report.finished_at = datetime.now(timezone.utc)
        await db.commit()


def start_report(factory, report_id, model, **kw) -> None:
    task = asyncio.create_task(write_report(factory, report_id, model, **kw))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def stale(report: AuditReport, now: datetime | None = None) -> bool:
    """A report still running after STALE_AFTER was lost (for example a restart)."""
    created = report.created_at if report.created_at.tzinfo else report.created_at.replace(tzinfo=timezone.utc)
    return report.status == "running" and (now or datetime.now(timezone.utc)) - created > STALE_AFTER


def public(report: AuditReport | None) -> dict | None:
    """What the teacher page receives: the text and the counted facts (not the question list)."""
    if report is None:
        return None
    facts = {k: v for k, v in (report.facts or {}).items() if k != "question_list"}
    lost = stale(report)
    return {"id": report.id, "lab_id": report.lab_id, "student_id": report.student_id,
            "status": "failed" if lost else report.status,
            "error": "interrupted" if lost else report.error,
            "content": report.content, "facts": facts or None, "model": report.model,
            "created_at": report.created_at, "finished_at": report.finished_at}
