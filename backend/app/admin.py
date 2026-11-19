"""Makes an account an admin, or an ordinary one again:

    python -m app.admin someone@example.com
    python -m app.admin someone@example.com --revoke
    python -m app.admin --list

An admin has no monthly cap on what it spends on the server's keys
(app/ai/routing.py). The email is looked up in SuperTokens, which owns it, so
the core has to be running.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select, update
from supertokens_python.asyncio import get_user, list_users_by_account_info
from supertokens_python.types.base import AccountInfoInput

from app.auth.supertokens import init_supertokens
from app.config import get_settings
from app.db.models import User
from app.db.session import get_engine, get_sessionmaker


async def set_admin(email: str, admin: bool) -> str | None:
    """The account's id once its flag is set; None when no account has that
    email."""
    found = await list_users_by_account_info("public", AccountInfoInput(email=email))
    if not found:
        return None
    user_id = uuid.UUID(found[0].id)
    async with get_sessionmaker()() as session:
        updated = await session.scalar(
            update(User).where(User.id == user_id).values(is_admin=admin).returning(User.id)
        )
        await session.commit()
    return str(updated) if updated else None


async def admins() -> list[str]:
    async with get_sessionmaker()() as session:
        ids = list(await session.scalars(select(User.id).where(User.is_admin).order_by(User.id)))
    emails = []
    for user_id in ids:
        login = await get_user(str(user_id))
        emails.append(login.emails[0] if login and login.emails else str(user_id))
    return emails


async def _main(args: argparse.Namespace) -> int:
    init_supertokens(get_settings())
    try:
        if args.list:
            # The operator's own terminal: this is the command's answer.
            sys.stdout.write("\n".join(await admins()) + "\n")
            return 0
        if await set_admin(args.email, not args.revoke) is None:
            sys.stderr.write(f"No account with the email {args.email}\n")
            return 1
        sys.stdout.write(f"{args.email} is {'no longer' if args.revoke else 'now'} an admin\n")
        return 0
    finally:
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin",
        description="Give an account no monthly spend cap, or take that away.",
    )
    parser.add_argument("email", nargs="?", help="the account's sign-in email")
    parser.add_argument("--revoke", action="store_true", help="make it an ordinary account again")
    parser.add_argument("--list", action="store_true", help="list the admins")
    args = parser.parse_args(argv)
    if not args.list and not args.email:
        parser.error("an email, or --list")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
