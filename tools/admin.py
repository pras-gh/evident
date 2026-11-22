#!/usr/bin/env python
"""Organizations, users, API keys and AI usage limits.

    python tools/admin.py create-org acme "Acme Capital" --monthly-spend 25
    python tools/admin.py create-user acme analyst@acme.com --role org_admin
    python tools/admin.py create-key analyst@acme.com --name laptop     # shown once
    python tools/admin.py set-limits acme --daily-requests 100 --monthly-tokens 5000000
    python tools/admin.py revoke-key plx_AbCdEfGh
    python tools/admin.py bootstrap-admin you@example.com               # platform admin + key
    python tools/admin.py list

Needs DATABASE_URL. A key is printed once and stored only as a hash: lose it
and revoke it, then make a new one.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("db", "graph"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))

from sqlalchemy import select  # noqa: E402

from parallx_db import (INTERNAL_ORG_SLUG, ApiKey, Organization, User,  # noqa: E402
                        session_scope)
from parallx_db.keys import display_prefix, hash_key, new_key  # noqa: E402

LIMIT_FLAGS = {"daily_requests": "daily_requests_per_user", "monthly_tokens": "monthly_tokens",
               "monthly_spend": "monthly_spend_usd", "max_input_tokens": "max_input_tokens",
               "max_output_tokens": "max_output_tokens"}


def die(msg: str) -> None:
    print(f"admin: {msg}", file=sys.stderr)
    raise SystemExit(1)


def org_by_slug(db, slug: str) -> Organization:
    org = db.execute(select(Organization).where(Organization.slug == slug)).scalar_one_or_none()
    if org is None:
        die(f"no organization {slug!r}")
    return org


def user_by_email(db, email: str) -> User:
    user = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
    if user is None:
        die(f"no user {email!r}")
    return user


def limits_from(args) -> dict:
    out = {}
    for flag, column in LIMIT_FLAGS.items():
        value = getattr(args, flag, None)
        if value is not None:
            out[column] = Decimal(value) if column == "monthly_spend_usd" else int(value)
    return out


def issue_key(db, user: User, name: str | None) -> str:
    key = new_key()
    db.add(ApiKey(user_id=user.id, key_hash=hash_key(key), prefix=display_prefix(key), name=name))
    return key


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def limit_args(p):
        p.add_argument("--daily-requests", dest="daily_requests", type=int)
        p.add_argument("--monthly-tokens", dest="monthly_tokens", type=int)
        p.add_argument("--monthly-spend", dest="monthly_spend", help="USD")
        p.add_argument("--max-input-tokens", dest="max_input_tokens", type=int)
        p.add_argument("--max-output-tokens", dest="max_output_tokens", type=int)

    p = sub.add_parser("create-org"); p.add_argument("slug"); p.add_argument("name"); limit_args(p)
    p = sub.add_parser("set-limits"); p.add_argument("slug"); limit_args(p)
    p = sub.add_parser("create-user"); p.add_argument("org"); p.add_argument("email")
    p.add_argument("--name"); p.add_argument("--role", default="member",
                                             choices=["member", "org_admin", "platform_admin"])
    p = sub.add_parser("create-key"); p.add_argument("email"); p.add_argument("--name")
    p = sub.add_parser("revoke-key"); p.add_argument("prefix")
    p = sub.add_parser("bootstrap-admin"); p.add_argument("email")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        die("set DATABASE_URL")

    with session_scope(url) as db:
        if args.cmd == "create-org":
            org = Organization(slug=args.slug, name=args.name, **limits_from(args))
            db.add(org)
            db.flush()
            print(f"organization {org.slug} ({org.id})")

        elif args.cmd == "set-limits":
            org = org_by_slug(db, args.slug)
            for column, value in limits_from(args).items():
                setattr(org, column, value)
            print(f"limits for {org.slug}: " + ", ".join(
                f"{c}={getattr(org, c)}" for c in LIMIT_FLAGS.values()))

        elif args.cmd == "create-user":
            org = org_by_slug(db, args.org)
            if args.role == "platform_admin" and not org.is_internal:
                die("platform_admin users belong to the internal organization")
            user = User(organization_id=org.id, email=args.email.lower(), name=args.name,
                        role=args.role)
            db.add(user)
            db.flush()
            print(f"user {user.email} ({user.role}) in {org.slug}")

        elif args.cmd == "create-key":
            user = user_by_email(db, args.email)
            key = issue_key(db, user, args.name)
            print(f"API key for {user.email} — shown once, store it now:\n\n  {key}\n")

        elif args.cmd == "revoke-key":
            k = db.execute(select(ApiKey).where(ApiKey.prefix == args.prefix,
                                                ApiKey.revoked_at.is_(None))).scalar_one_or_none()
            if k is None:
                die(f"no active key starting {args.prefix!r}")
            k.revoked_at = datetime.now(timezone.utc)
            print(f"revoked {k.prefix}")

        elif args.cmd == "bootstrap-admin":
            org = db.execute(select(Organization).where(
                Organization.slug == INTERNAL_ORG_SLUG)).scalar_one_or_none()
            if org is None:
                org = Organization(slug=INTERNAL_ORG_SLUG, name="Parallx (internal)", is_internal=True)
                db.add(org)
                db.flush()
            user = db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
            if user is None:
                user = User(organization_id=org.id, email=args.email.lower(), role="platform_admin")
                db.add(user)
                db.flush()
            elif user.organization_id != org.id or user.role != "platform_admin":
                die(f"{args.email} exists and is not a platform admin")
            key = issue_key(db, user, "bootstrap")
            print(f"platform admin {user.email} — key shown once:\n\n  {key}\n")

        elif args.cmd == "list":
            for org in db.execute(select(Organization).order_by(Organization.slug)).scalars():
                limits = {c: getattr(org, c) for c in LIMIT_FLAGS.values() if getattr(org, c) is not None}
                print(f"{org.slug}{' (internal)' if org.is_internal else ''}  {org.name}"
                      + (f"  limits {limits}" if limits else ""))
                for u in db.execute(select(User).where(User.organization_id == org.id)).scalars():
                    keys = db.execute(select(ApiKey).where(ApiKey.user_id == u.id)).scalars().all()
                    shown = ", ".join(k.prefix + ("(revoked)" if k.revoked_at else "") for k in keys)
                    print(f"    {u.email} [{u.role}]  keys: {shown or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
