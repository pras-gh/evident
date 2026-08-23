#!/usr/bin/env python3
"""Regenerate db/schema.sql from db/migrations/*.sql."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
mig = sorted((ROOT / "db" / "migrations").glob("*.sql"))
head = (ROOT / "db" / "schema.sql").read_text().split("-- ===== ")[0]
body = "\n\n".join(f"-- ===== {m.name} " + "=" * (66 - len(m.name)) + "\n\n"
                   + m.read_text().strip() for m in mig)
(ROOT / "db" / "schema.sql").write_text(head + body + "\n")
print(f"db/schema.sql rebuilt from {len(mig)} migrations")
