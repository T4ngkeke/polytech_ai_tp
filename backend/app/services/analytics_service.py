"""
services/analytics_service.py — Hierarchical token usage analytics.

Provides aggregated analytics at class→lab→student granularity.
Both Teacher and Admin routers call these functions — Teacher passes
class_id with ownership already verified; Admin passes it directly.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    AgentRun, ClassStudent, Lab, Message, QuotaGrant, SenderType, Session, UsageStat, User,
)


@dataclass
class StudentUsage:
    user_id: uuid.UUID
    username: str
    tokens_used: int
    request_count: int


@dataclass
class LabUsage:
    lab_id: uuid.UUID
    lab_name: str
    tokens: int
    requests: int
    students: list[StudentUsage] = field(default_factory=list)


@dataclass
class ClassAnalytics:
    class_id: uuid.UUID
    class_name: str
    total_tokens: int
    total_requests: int
    labs: list[LabUsage] = field(default_factory=list)


@dataclass
class GlobalAnalytics:
    total_tokens: int
    total_requests: int
    daily_breakdown: list[dict] = field(default_factory=list)
    active_users_7d: int = 0


async def get_class_analytics(db: AsyncSession, class_id: uuid.UUID, class_name: str) -> ClassAnalytics:
    """
    Build a full hierarchical analytics report for a class.
    Returns: ClassAnalytics with per-lab and per-student breakdown.
    """
    # Get all labs for this class
    labs_result = await db.execute(
        select(Lab).where(Lab.class_id == class_id, Lab.is_deleted.is_(False))
    )
    labs = labs_result.scalars().all()

    class_total_tokens = 0
    class_total_requests = 0
    lab_usages: list[LabUsage] = []

    for lab in labs:
        # Aggregate per-student usage for this lab. A "request" is one exchange =
        # one llm reply, so count only llm messages (counting all Message rows
        # would double it by including the paired user message), consistent with
        # UsageStat.request_count used by the global dashboard.
        stats_result = await db.execute(
            select(
                Session.user_id,
                func.coalesce(func.sum(Message.billed_tokens), 0),
                func.count(Message.id).filter(Message.sender == SenderType.llm),
            )
            .join(Message, Message.session_id == Session.id)
            # Teacher test-drives never count toward class usage.
            .where(Session.lab_id == lab.id, Session.is_test.is_(False))
            .group_by(Session.user_id)
        )
        user_stats = stats_result.all()

        lab_tokens = sum(r[1] for r in user_stats)
        lab_requests = sum(r[2] for r in user_stats)
        class_total_tokens += lab_tokens
        class_total_requests += lab_requests

        students: list[StudentUsage] = []
        for user_id, tokens, req_count in user_stats:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if user:
                students.append(StudentUsage(
                    user_id=user.id,
                    username=user.username,
                    tokens_used=tokens,
                    request_count=req_count,
                ))

        lab_usages.append(LabUsage(
            lab_id=lab.id,
            lab_name=lab.name,
            tokens=lab_tokens,
            requests=lab_requests,
            students=students,
        ))

    return ClassAnalytics(
        class_id=class_id,
        class_name=class_name,
        total_tokens=class_total_tokens,
        total_requests=class_total_requests,
        labs=lab_usages,
    )


async def get_global_analytics(db: AsyncSession) -> GlobalAnalytics:
    """Platform-wide token usage summary with 30-day daily breakdown."""
    totals_result = await db.execute(
        select(
            func.coalesce(func.sum(UsageStat.tokens_used), 0),
            func.coalesce(func.sum(UsageStat.request_count), 0),
        )
    )
    row = totals_result.one()
    total_tokens = row[0]
    total_requests = row[1]

    daily_result = await db.execute(
        select(
            UsageStat.date,
            func.sum(UsageStat.tokens_used),
            func.sum(UsageStat.request_count),
        )
        .group_by(UsageStat.date)
        .order_by(UsageStat.date.desc())
        .limit(30)
    )
    daily_breakdown = [
        {"date": str(r[0]), "tokens": r[1], "requests": r[2]}
        for r in daily_result.all()
    ]
    active_users_7d = await db.scalar(
        select(func.count(func.distinct(UsageStat.user_id))).where(
            UsageStat.date > date.today() - timedelta(days=7), UsageStat.request_count > 0))

    return GlobalAnalytics(
        total_tokens=total_tokens,
        total_requests=total_requests,
        daily_breakdown=daily_breakdown,
        active_users_7d=active_users_7d or 0,
    )


async def get_class_overview(db: AsyncSession, class_ids: list[uuid.UUID], *,
                             now: datetime | None = None) -> dict[uuid.UUID, dict]:
    """Counts for the admin Classes list: labs, students, questions in the
    last 7 days and the last question, per class. Teacher test sessions
    are left out."""
    now = now or datetime.now(timezone.utc)
    out = {cid: {"lab_count": 0, "student_count": 0, "questions_7d": 0, "last_active": None} for cid in class_ids}
    if not class_ids:
        return out
    for cid, n in (await db.execute(
            select(Lab.class_id, func.count(Lab.id))
            .where(Lab.class_id.in_(class_ids), Lab.is_deleted.is_(False))
            .group_by(Lab.class_id))).all():
        out[cid]["lab_count"] = n
    for cid, n in (await db.execute(
            select(ClassStudent.class_id, func.count(ClassStudent.student_id))
            .join(User, User.id == ClassStudent.student_id)
            .where(ClassStudent.class_id.in_(class_ids), User.is_deleted.is_(False))
            .group_by(ClassStudent.class_id))).all():
        out[cid]["student_count"] = n
    recent = now - timedelta(days=7)
    for cid, n, last in (await db.execute(
            select(Lab.class_id,
                   func.count(Message.id).filter(Message.created_at >= recent),
                   func.max(Message.created_at))
            .join(Session, Session.lab_id == Lab.id)
            .join(Message, Message.session_id == Session.id)
            .where(Lab.class_id.in_(class_ids), Session.is_test.is_(False),
                   Message.sender == SenderType.user)
            .group_by(Lab.class_id))).all():
        out[cid]["questions_7d"] = n
        out[cid]["last_active"] = last
    return out


# ---------------------------------------------------------------------------
# Teacher analytics: activity, students, quotas and answers that went wrong
# ---------------------------------------------------------------------------

PARIS = ZoneInfo("Europe/Paris")
PERIOD_DAYS = 30          # daily chart and hour-of-week grid
LIST_LIMIT = 20           # thumbs-down and problem lists
PROBLEM_STOPS = {"budget_exhausted": "Tool limit reached", "no_answer": "No answer"}


def _local(moment: datetime) -> datetime:
    if moment.tzinfo is None:          # SQLite returns naive UTC
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(PARIS)


async def get_teaching_analytics(db: AsyncSession, class_id: uuid.UUID,
                                 lab_id: uuid.UUID | None = None, *,
                                 now: datetime | None = None) -> dict:
    """Everything the teacher Analytics tab shows, for a class or one of its labs.

    Questions are student messages; modes and thumbs are on the answers.
    Teacher test sessions are left out. Days and hours are in Paris time.
    Token figures for today come from the quota (all classes of the student).
    """
    now = now or datetime.now(timezone.utc)
    today_local = _local(now).date()
    since = now - timedelta(days=PERIOD_DAYS)

    labs = {lab.id: lab.name for lab in (await db.scalars(
        select(Lab).where(Lab.class_id == class_id, Lab.is_deleted.is_(False)))).all()}
    lab_ids = [lab_id] if lab_id is not None else list(labs)
    members = (await db.execute(
        select(User).join(ClassStudent, ClassStudent.student_id == User.id)
        .where(ClassStudent.class_id == class_id, User.is_deleted.is_(False))
        .order_by(User.username))).scalars().all()

    rows = (await db.execute(
        select(Message.id, Message.session_id, Message.sender, Message.created_at, Message.answer_mode,
               Message.feedback, Message.billed_tokens, Message.content, Session.user_id, Session.lab_id)
        .join(Session, Session.id == Message.session_id)
        .where(Session.lab_id.in_(lab_ids), Session.is_test.is_(False))
        .order_by(Message.created_at))).all() if lab_ids else []

    per_student: dict[uuid.UUID, dict] = {}
    def student(user_id):
        return per_student.setdefault(user_id, {
            "questions": 0, "sessions": set(), "last_active": None, "tokens": 0,
            "thumbs_down": 0, "modes": {}})

    daily = {today_local - timedelta(days=i): 0 for i in range(PERIOD_DAYS)}
    heatmap = [[0] * 24 for _ in range(7)]
    modes: dict[str, int] = {}
    overview = {"questions": 0, "questions_7d": 0, "thumbs_up": 0, "thumbs_down": 0}
    sessions, active_7d = set(), set()
    last_question: dict[uuid.UUID, tuple] = {}      # session -> its latest question
    thumbs_down = []
    for r in rows:
        s = student(r.user_id)
        s["sessions"].add(r.session_id)
        sessions.add(r.session_id)
        local = _local(r.created_at)
        if r.sender == SenderType.user:
            overview["questions"] += 1
            s["questions"] += 1
            s["last_active"] = max(s["last_active"] or r.created_at, r.created_at)
            last_question[r.session_id] = (r.content, r.created_at)
            if local.date() in daily:
                daily[local.date()] += 1
            if _local(since) <= local:
                heatmap[local.weekday()][local.hour] += 1
            if now - timedelta(days=7) <= local:
                overview["questions_7d"] += 1
                active_7d.add(r.user_id)
        elif r.sender == SenderType.llm:
            s["tokens"] += r.billed_tokens or 0
            if r.answer_mode:
                modes[r.answer_mode] = modes.get(r.answer_mode, 0) + 1
                s["modes"][r.answer_mode] = s["modes"].get(r.answer_mode, 0) + 1
            feedback = getattr(r.feedback, "value", r.feedback)
            if feedback == "up":
                overview["thumbs_up"] += 1
            elif feedback == "down":
                overview["thumbs_down"] += 1
                s["thumbs_down"] += 1
                question, asked = last_question.get(r.session_id, ("", r.created_at))
                thumbs_down.append({"message_id": r.id, "session_id": r.session_id, "user_id": r.user_id,
                                    "lab_id": r.lab_id, "question": question[:300], "at": asked})

    runs = (await db.execute(
        select(AgentRun.session_id, AgentRun.user_id, AgentRun.question, AgentRun.status,
               AgentRun.stop_reason, AgentRun.started_at, Session.lab_id)
        .join(Session, Session.id == AgentRun.session_id)
        .where(Session.lab_id.in_(lab_ids), Session.is_test.is_(False),
               (AgentRun.status == "failed") | AgentRun.stop_reason.in_(PROBLEM_STOPS))
        .order_by(AgentRun.started_at.desc()).limit(LIST_LIMIT))).all() if lab_ids else []

    names = {u.id: u.username for u in members}
    missing = {r.user_id for r in rows} - set(names)
    if missing:          # students who left the class keep their history
        names.update({u.id: u.username for u in (await db.scalars(select(User).where(User.id.in_(missing)))).all()})

    member_ids = [u.id for u in members]
    today = date.today()
    used_today = dict((await db.execute(select(UsageStat.user_id, UsageStat.tokens_used).where(
        UsageStat.user_id.in_(member_ids), UsageStat.date == today))).all()) if member_ids else {}
    granted_today: dict[uuid.UUID, int] = {}
    if member_ids:
        for user_id, tokens in (await db.execute(
                select(QuotaGrant.user_id, func.sum(QuotaGrant.tokens)).where(
                    QuotaGrant.user_id.in_(member_ids), QuotaGrant.date == today)
                .group_by(QuotaGrant.user_id))).all():
            granted_today[user_id] = int(tokens)

    students = []
    for user_id in member_ids + sorted(missing, key=lambda i: names.get(i, "")):
        s = per_student.get(user_id) or student(user_id)
        quota = next((u.daily_token_quota for u in members if u.id == user_id), None)
        limit = quota + granted_today.get(user_id, 0) if quota is not None else None
        students.append({
            "user_id": user_id, "username": names.get(user_id, "?"), "member": user_id in names and quota is not None,
            "questions": s["questions"], "sessions": len(s["sessions"]), "last_active": s["last_active"],
            "tokens": s["tokens"], "thumbs_down": s["thumbs_down"], "modes": s["modes"],
            "used_today": used_today.get(user_id, 0), "limit_today": limit,
            "granted_today": granted_today.get(user_id, 0),
            "at_limit": limit is not None and used_today.get(user_id, 0) >= limit,
        })

    return {
        "class_id": class_id, "lab_id": lab_id, "period_days": PERIOD_DAYS,
        "overview": {**overview, "members": len(members),
                     "active_7d": len(active_7d & set(member_ids)),
                     "active_ever": len({u for u, s in per_student.items() if s["questions"]} & set(member_ids)),
                     "sessions": len(sessions),
                     "at_limit_today": sum(1 for s in students if s["at_limit"])},
        "modes": modes,
        "daily": [{"date": d, "questions": n} for d, n in sorted(daily.items())],
        "heatmap": heatmap,
        "students": students,
        "thumbs_down": [{**t, "username": names.get(t["user_id"], "?"), "lab_name": labs.get(t["lab_id"], "")}
                        for t in reversed(thumbs_down)][:LIST_LIMIT],
        "problems": [{"session_id": r.session_id, "user_id": r.user_id, "username": names.get(r.user_id, "?"),
                      "lab_id": r.lab_id, "lab_name": labs.get(r.lab_id, ""), "question": (r.question or "")[:300],
                      "at": r.started_at,
                      "reason": "Failed" if r.status == "failed" else PROBLEM_STOPS.get(r.stop_reason, r.stop_reason)}
                     for r in runs],
    }
