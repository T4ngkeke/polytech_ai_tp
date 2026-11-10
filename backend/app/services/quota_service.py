"""
services/quota_service.py — a student's token limits for the day and the week.

Days start at midnight Paris time, weeks on Monday. A limit is the user's
quota plus what teachers granted for that day or week (QuotaGrant). Usage
(UsageStat) is never rewritten, so the statistics stay true.

The rule is checked once, when a question starts (routers/chat.py): a
question may start while both limits have room, and a started question
always finishes, even when it goes over. The next one is refused.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import QuotaGrant, QuotaResetRequest, UsageStat, User

PARIS = ZoneInfo("Europe/Paris")


def today(now: datetime | None = None) -> date:
    """The quota day: the date in Paris."""
    return (now or datetime.now(timezone.utc)).astimezone(PARIS).date()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _paris_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=PARIS).astimezone(timezone.utc)


@dataclass
class Usage:
    used: int            # today
    limit: int           # today: quota + day grants
    granted: int
    week_used: int
    week_limit: int
    week_granted: int
    day: date

    @property
    def blocked(self) -> str | None:
        """'day' or 'week' when a new question may not start, else None."""
        if self.used >= self.limit:
            return "day"
        if self.week_used >= self.week_limit:
            return "week"
        return None

    @property
    def resets_at(self) -> datetime:
        """When the blocking limit (or else the day) starts again."""
        if self.blocked == "week" and self.used < self.limit:
            return _paris_midnight(week_start(self.day) + timedelta(days=7))
        return _paris_midnight(self.day + timedelta(days=1))

    @property
    def week_resets_at(self) -> datetime:
        return _paris_midnight(week_start(self.day) + timedelta(days=7))


async def _granted(db: AsyncSession, user_ids: list[uuid.UUID], day: date) -> dict:
    """{user_id: (day grants, week grants)}."""
    out = {u: [0, 0] for u in user_ids}
    if not user_ids:
        return {}
    start = week_start(day)
    rows = (await db.execute(
        select(QuotaGrant.user_id, QuotaGrant.kind, QuotaGrant.date, QuotaGrant.tokens).where(
            QuotaGrant.user_id.in_(user_ids),
            QuotaGrant.date >= start, QuotaGrant.date <= day))).all()
    for user_id, kind, when, tokens in rows:
        if kind == "week":
            out[user_id][1] += tokens
        elif when == day:
            out[user_id][0] += tokens
    return {u: tuple(v) for u, v in out.items()}


async def _used(db: AsyncSession, user_ids: list[uuid.UUID], day: date) -> dict:
    """{user_id: (used today, used this week)}."""
    out = {u: [0, 0] for u in user_ids}
    if not user_ids:
        return {}
    rows = (await db.execute(
        select(UsageStat.user_id, UsageStat.date, UsageStat.tokens_used).where(
            UsageStat.user_id.in_(user_ids),
            UsageStat.date >= week_start(day), UsageStat.date <= day))).all()
    for user_id, when, tokens in rows:
        out[user_id][1] += tokens
        if when == day:
            out[user_id][0] += tokens
    return {u: tuple(v) for u, v in out.items()}


async def usages(db: AsyncSession, users: list[User], day: date | None = None) -> dict[uuid.UUID, Usage]:
    """Usage of several users with two queries."""
    day = day or today()
    ids = [u.id for u in users]
    used, granted = await _used(db, ids, day), await _granted(db, ids, day)
    return {u.id: Usage(
        used=used[u.id][0], limit=u.daily_token_quota + granted[u.id][0], granted=granted[u.id][0],
        week_used=used[u.id][1], week_limit=u.weekly_token_quota + granted[u.id][1],
        week_granted=granted[u.id][1], day=day) for u in users}


async def usage(db: AsyncSession, user: User, day: date | None = None) -> Usage:
    return (await usages(db, [user], day))[user.id]


async def open_requests(db: AsyncSession, user_ids: list[uuid.UUID],
                        day: date | None = None) -> dict[uuid.UUID, datetime]:
    """{user_id: when asked} for today's unresolved reset requests."""
    if not user_ids:
        return {}
    rows = (await db.execute(select(QuotaResetRequest.user_id, QuotaResetRequest.created_at).where(
        QuotaResetRequest.user_id.in_(user_ids), QuotaResetRequest.date == (day or today()),
        QuotaResetRequest.resolved_at.is_(None)))).all()
    return dict(rows)


async def reset_state(db: AsyncSession, user_id: uuid.UUID) -> tuple[str | None, datetime | None]:
    """How today's last request stands: ('asked', when asked), ('reset' or
    'ignored', when answered), or (None, None) without a request today."""
    row = (await db.execute(select(QuotaResetRequest.outcome, QuotaResetRequest.created_at,
                                   QuotaResetRequest.resolved_at)
        .where(QuotaResetRequest.user_id == user_id, QuotaResetRequest.date == today())
        .order_by(QuotaResetRequest.created_at.desc()).limit(1))).first()
    if row is None:
        return None, None
    return (row.outcome or "asked"), (row.resolved_at or row.created_at)


async def request_reset(db: AsyncSession, user: User) -> bool:
    """The student asks a teacher for a reset. False when a request of today
    is still open (one at a time) or was ignored (not again that day).
    After a reset the student can ask again."""
    day = today()
    existing = await db.scalar(select(QuotaResetRequest.id).where(
        QuotaResetRequest.user_id == user.id, QuotaResetRequest.date == day,
        QuotaResetRequest.resolved_at.is_(None) | (QuotaResetRequest.outcome == "ignored")))
    if existing is not None:
        return False
    db.add(QuotaResetRequest(user_id=user.id, date=day))
    await db.flush()
    return True


async def refill(db: AsyncSession, *, student: User, by: uuid.UUID) -> Usage:
    """Give the student a full day and a full week again from now on: grant
    what was used beyond earlier grants. Nothing is granted for a limit that
    is still full. Today's reset request, if any, is answered."""
    current = await usage(db, student)
    day_tokens = current.used - current.granted
    week_tokens = current.week_used - current.week_granted
    stamp = datetime.now(timezone.utc)  # one refill: its grants share the time
    for kind, tokens in (("day", day_tokens), ("week", week_tokens)):
        if tokens > 0:
            db.add(QuotaGrant(user_id=student.id, date=current.day, tokens=tokens,
                              kind=kind, granted_by=by, created_at=stamp))
    await db.execute(update(QuotaResetRequest).where(
        QuotaResetRequest.user_id == student.id, QuotaResetRequest.date == current.day,
        QuotaResetRequest.resolved_at.is_(None)).values(resolved_at=stamp, resolved_by=by, outcome="reset"))
    await db.flush()
    return await usage(db, student)


async def ignore_request(db: AsyncSession, *, student_id: uuid.UUID, by: uuid.UUID, undo: bool = False) -> bool:
    """A teacher ignores today's open request (the limits stay; the student
    cannot ask again today), or undoes that. False when there is nothing to do."""
    day = today()
    if undo:
        query = update(QuotaResetRequest).where(
            QuotaResetRequest.user_id == student_id, QuotaResetRequest.date == day,
            QuotaResetRequest.outcome == "ignored").values(resolved_at=None, resolved_by=None, outcome=None)
    else:
        query = update(QuotaResetRequest).where(
            QuotaResetRequest.user_id == student_id, QuotaResetRequest.date == day,
            QuotaResetRequest.resolved_at.is_(None)).values(
            resolved_at=datetime.now(timezone.utc), resolved_by=by, outcome="ignored")
    result = await db.execute(query.execution_options(synchronize_session=False))
    await db.flush()
    return result.rowcount > 0


async def reset_count(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many times each user was refilled (one refill may add a day and a week grant)."""
    if not user_ids:
        return {}
    rows = (await db.execute(select(QuotaGrant.user_id, func.count(func.distinct(QuotaGrant.created_at)))
                             .where(QuotaGrant.user_id.in_(user_ids)).group_by(QuotaGrant.user_id))).all()
    return dict(rows)
