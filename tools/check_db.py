#!/usr/bin/env python
"""Check that DATABASE_URL is a database the API can serve from.

    python tools/check_db.py

Four things, each with the fix when it fails: the server answers, it is
PostgreSQL 15+ (migration 0009 uses NULLS NOT DISTINCT), pgvector is enabled,
and the schema is at the newest migration. `tools/dev.sh api` runs this first,
so a wrong database fails at startup with a sentence rather than on the first
request with a stack trace.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = "tools/db.sh up starts and migrates a local one."


def newest_migration() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(str(ROOT / "db" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(f"check_db: DATABASE_URL is not set. {FIX}", file=sys.stderr)
        return 1

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    where = make_url(url).render_as_string(hide_password=True)
    try:
        with create_engine(url).connect() as c:
            version = c.execute(text("show server_version_num")).scalar()
            vector = c.execute(text(
                "select 1 from pg_extension where extname = 'vector'")).scalar()
            has_table = c.execute(text("select to_regclass('alembic_version')")).scalar()
            head = (c.execute(text("select version_num from alembic_version")).scalar()
                    if has_table else None)
    except Exception as exc:  # the message is the useful part, whatever the class
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        print(f"check_db: cannot connect to {where}: {first}\n  {FIX}", file=sys.stderr)
        return 1

    problems = []
    if int(version) < 150000:
        problems.append(f"PostgreSQL {int(version) // 10000} is too old; Evident needs 15+ "
                        "(migration 0009 uses NULLS NOT DISTINCT).")
    if not vector:
        problems.append("the pgvector extension is not enabled "
                        "(create extension vector — tools/db.sh up does this).")
    wanted = newest_migration()
    if head != wanted:
        problems.append(f"schema is at migration {head or 'none'}, code expects {wanted} "
                        "(cd db && alembic upgrade head).")
    if problems:
        print(f"check_db: {where}:", *[f"  - {p}" for p in problems], sep="\n",
              file=sys.stderr)
        return 1
    print(f"check_db: {where} — PostgreSQL {int(version) // 10000}, pgvector, migration {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
