"""Copy a v6 database into a new one: accounts, classes, labs, rules, conversations.

Usage:
  python -m scripts.migrate_from_v6 --source V6_URL --target NEW_URL [--apply]

The target is the database of the new version, started once so its tables
exist, with no user yet. Copied as they are (same ids, so invite codes and
links keep working): users with their password hashes, roles and quotas;
classes; class memberships; labs (open or closed); rules of the three levels;
conversations (sessions and messages, deleted ones too, so the Audit keeps
them) and the daily usage. Every copied class is put in exam mode (Direct
answers, no course search): what v6 did. A v6 answer has no recorded mode
(answer_mode NULL), as the answers written before modes were recorded. The
v6 model settings are not copied: the new version has its own.

Without --apply it reads both databases, checks, prints what it would copy,
and changes nothing. The source is only read.
"""
import argparse
import asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.models import (
    Class, ClassStudent, Lab, Message, Rule, RuleLevel, SenderType, Session, UsageStat, User, UserRole,
)

TABLES = {
    'users': "SELECT id, username, hashed_password, role::text AS role, daily_token_quota, is_deleted FROM users",
    'classes': "SELECT id, name, teacher_id, invite_code, is_deleted, created_at FROM classes",
    'class_students': "SELECT class_id, student_id, joined_at FROM class_students",
    'labs': "SELECT id, class_id, name, is_deleted, is_active, created_at FROM labs",
    'rules': "SELECT id, level::text AS level, target_id, rules_text, is_active FROM rules",
    'sessions': "SELECT id, user_id, lab_id, title, is_deleted, created_at FROM sessions",
    'messages': ("SELECT id, session_id, sender::text AS sender, content, prompt_tokens, completion_tokens, "
                 "total_tokens, created_at FROM messages"),
    'usage_stats': "SELECT id, user_id, date, tokens_used, request_count FROM usage_stats",
}


async def read_source(url: str) -> dict[str, list[dict]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SET TRANSACTION READ ONLY'))
            return {name: [dict(r._mapping) for r in await conn.execute(text(sql))]
                    for name, sql in TABLES.items()}
    finally:
        await engine.dispose()


def rows_to_objects(data: dict[str, list[dict]]) -> list:
    """Current ORM objects, in an order the foreign keys accept."""
    def enum(kind, value):
        # Enum columns may come back as the member name or its value.
        return next(m for m in kind if value in (m.value, m.name))
    return ([User(**{**u, 'role': enum(UserRole, u['role'])}) for u in data['users']]
            + [Class(**c, exam_mode=True) for c in data['classes']]
            + [ClassStudent(**m) for m in data['class_students']]
            + [Lab(**lab) for lab in data['labs']]
            + [Rule(**{**r, 'level': enum(RuleLevel, r['level'])}) for r in data['rules']]
            + [Session(**s) for s in data.get('sessions', [])]
            + [Message(**{**m, 'sender': enum(SenderType, m['sender'])}) for m in data.get('messages', [])]
            + [UsageStat(**u) for u in data.get('usage_stats', [])])


def summary(data: dict[str, list[dict]]) -> str:
    roles = {}
    for u in data['users']:
        roles[u['role']] = roles.get(u['role'], 0) + 1
    return (f"users {len(data['users'])} {roles}, classes {len(data['classes'])}, "
            f"memberships {len(data['class_students'])}, labs {len(data['labs'])}, rules {len(data['rules'])}, "
            f"sessions {len(data.get('sessions', []))}, messages {len(data.get('messages', []))}, "
            f"usage days {len(data.get('usage_stats', []))}")


async def write_target(url: str, data: dict[str, list[dict]], *, apply: bool) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            existing = await db.scalar(select(func.count(User.id)))
            if existing:
                raise SystemExit(f'The target already has {existing} users: refusing to mix two installations.')
            db.add_all(rows_to_objects(data))
            await db.flush()               # checks every constraint
            if apply:
                await db.commit()
            else:
                await db.rollback()
    finally:
        await engine.dispose()


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', required=True, help='v6 database URL (read only)')
    parser.add_argument('--target', required=True, help='new database URL (no users yet)')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    data = await read_source(args.source)
    print('source:', summary(data))
    await write_target(args.target, data, apply=args.apply)
    print('copied; every class is in exam mode' if args.apply else 'dry run: checked, nothing written (add --apply)')


if __name__ == '__main__':
    asyncio.run(main())
