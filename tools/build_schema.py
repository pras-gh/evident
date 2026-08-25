#!/usr/bin/env python3
"""Regenerate db/schema.sql from the Alembic chain.

The file used to be concatenated from hand-written SQL that stopped matching
the models — the README told people to apply it, and doing so produced a
database the code could not read. Generating it from the migrations means there
is one source of truth and the generated file cannot drift from it.

    python3 tools/build_schema.py
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADER = """-- evident — full schema, generated from the Alembic chain.
--
-- DO NOT EDIT. Regenerate with:
--     python3 tools/build_schema.py
--
-- Migrations are the source of truth and live in db/alembic/versions/. Apply
-- this file to a brand-new database, or run `alembic upgrade head` against
-- anything that already exists.

"""


def main() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT / "db", capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL":
             "postgresql+psycopg://localhost/evident_offline_render",
             "HOME": str(pathlib.Path.home())},
    )
    if out.returncode != 0:
        print(out.stderr.strip()[-1500:], file=sys.stderr)
        return out.returncode

    body = "\n".join(line for line in out.stdout.splitlines()
                     if not line.startswith("-- Running upgrade"))
    target = ROOT / "db" / "schema.sql"
    target.write_text(HEADER + body.strip() + "\n")
    tables = body.count("CREATE TABLE")
    print(f"db/schema.sql regenerated — {tables} CREATE TABLE statements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
