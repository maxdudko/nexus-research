"""Operator commands for accounts. Passwords are read from stdin only."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import TextIO

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus.core.config import Settings, get_settings
from nexus.identity.errors import IdentityError
from nexus.identity.store import (
    any_admin,
    bootstrap_admin,
    create_user,
    disable_user,
    get_user_by_email,
    set_password,
)
from nexus.infrastructure.database import create_db_engine, session_factory

_MESSAGES = {
    "invalid_email": "Enter a valid email address.",
    "invalid_password": "Password must be 12 to 128 characters.",
    "password_matches_email": "Password must not match the email address.",
    "duplicate_email": "An account with that email already exists.",
    "not_found": "No account was found for that email.",
    "quarantine": "That account cannot be changed.",
    "invalid_role": "The account role is not supported.",
}


def main(argv: list[str] | None = None) -> None:
    """Exit 0 after a successful or idempotent operator command."""
    raise SystemExit(run_cli(argv))


def run_cli(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
    settings: Settings | None = None,
) -> int:
    """Run one identity command. Database errors stay off the console."""
    err = sys.stderr if stderr is None else stderr
    reader = sys.stdin if stdin is None else stdin
    args = _parser().parse_args(argv)
    resolved = settings if settings is not None else get_settings()
    try:
        return asyncio.run(_run(args, reader, err, resolved))
    except IdentityError as exc:
        print(_MESSAGES.get(exc.code, "The account could not be changed."), file=err)
        return 1
    except Exception:
        print("The identity store is unavailable.", file=err)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nexus.identity")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap-admin")
    bootstrap.add_argument("email")
    create = sub.add_parser("create-user")
    create.add_argument("email")
    create.add_argument("--admin", action="store_true")
    disable = sub.add_parser("disable-user")
    disable.add_argument("email")
    reset = sub.add_parser("reset-password")
    reset.add_argument("email")
    return parser


async def _run(
    args: argparse.Namespace,
    stdin: TextIO,
    stderr: TextIO,
    settings: Settings,
) -> int:
    engine = create_db_engine(str(settings.database_url))
    factory = session_factory(engine)
    try:
        return await _command(args, stdin, stderr, factory)
    finally:
        await engine.dispose()


async def _command(
    args: argparse.Namespace,
    stdin: TextIO,
    stderr: TextIO,
    factory: async_sessionmaker[AsyncSession],
) -> int:
    now = datetime.now(UTC)
    if args.command == "bootstrap-admin":
        if await any_admin(factory):
            print("administrator already provisioned", file=stderr)
            return 0
        created = await bootstrap_admin(factory, args.email, _password(stdin), now)
        if created is None:
            print("administrator already provisioned", file=stderr)
        return 0
    if args.command == "create-user":
        role = "admin" if args.admin else "user"
        await create_user(factory, args.email, _password(stdin), role=role, now=now)
        return 0
    if args.command == "disable-user":
        await disable_user(factory, args.email, now)
        return 0
    if args.command == "reset-password":
        account = await get_user_by_email(factory, args.email)
        if account is None or account.status == "quarantine":
            raise IdentityError("not_found" if account is None else "quarantine")
        await set_password(factory, account.id, _password(stdin), now, audit=True)
        return 0
    print("The account command is not supported.", file=stderr)
    return 1


def _password(stdin: TextIO) -> str:
    line = stdin.readline()
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line
