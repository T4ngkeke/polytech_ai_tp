"""Give an account the admin role again, on the server, when no admin can sign in.

Usage: python -m scripts.make_admin --database-url URL --username NAME [--reset-password]

The app does not let an admin delete their own account or change their own
role, so one admin always stays. This script is for the other cases (a lost
password, an account deleted in the database). It undeletes the account, makes
it an admin and, with --reset-password, sets the reset password (settings.
RESET_PASSWORD, 123456 by default) that must be changed at the next sign-in.
"""
import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.auth import hash_password
from backend.app.config import settings
from backend.app.models import User, UserRole


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--reset-password', action='store_true')
    args = parser.parse_args()
    engine = create_async_engine(args.database_url)
    try:
        async with async_sessionmaker(engine)() as db:
            user = await db.scalar(select(User).where(User.username == args.username))
            if user is None:
                raise SystemExit(f'No account named {args.username!r}.')
            user.role, user.is_deleted = UserRole.admin, False
            if args.reset_password:
                user.hashed_password = hash_password(settings.RESET_PASSWORD)
                user.must_change_password = True
            await db.commit()
    finally:
        await engine.dispose()
    print(f'{args.username}: admin' + (' (password reset, to change at sign-in)' if args.reset_password else ''))


if __name__ == '__main__':
    asyncio.run(main())
