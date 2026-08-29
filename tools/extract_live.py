#!/usr/bin/env python
"""Run the real extraction pipeline against Claude, once, and report.

    chunk -> Claude API -> JSON Schema -> Pydantic -> entities + relationships -> Postgres

This is the command that turns the pipeline from "built" into "proven". It uses
the same `build_for_document` the worker does — no parallel code path, so a
green run here means the worker works, not that a demo script does.

    # cheapest useful run: three chunks of the newest NVDA filing
    python tools/extract_live.py --ticker NVDA --limit 3

    # see exactly what would be sent, without sending it
    python tools/extract_live.py --ticker NVDA --limit 1 --dry-run

Start with `--limit`. A 10-K is a few hundred chunks, and the first thing worth
knowing is the drop rate on three of them, not the bill for all of them.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path.insert(0, str(ROOT))


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--accession", help="default: the newest filing on file")
    ap.add_argument("--limit", type=int, default=None,
                    help="only extract this many chunks (do this first)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the request for the first chunk and stop")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s")

    from sqlalchemy import select

    from evident_ai.extract import cache_hit_rate, request_params
    from evident_db import Chunk, Company, Document, session_scope
    from evident_parser.models import Block

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _die("DATABASE_URL is not set. This runs against a real database, "
             "because the point is to prove the whole path.")

    with session_scope(dsn) as db:
        company = db.execute(select(Company).where(
            Company.ticker == args.ticker.upper())).scalars().first()
        if company is None:
            _die(f"no company {args.ticker!r} — ingest a filing first "
                 "(POST /ingest)")

        stmt = select(Document).where(Document.company_id == company.id)
        if args.accession:
            stmt = stmt.where(Document.accession == args.accession)
        document = db.execute(
            stmt.order_by(Document.filed_at.desc())).scalars().first()
        if document is None:
            _die(f"no documents on file for {args.ticker}")

        chunks = list(db.execute(
            select(Chunk).where(Chunk.document_id == document.id)
            .order_by(Chunk.ordinal)).scalars())
        print(f"  {args.ticker.upper()} {document.form_type} {document.accession}"
              f" — {len(chunks)} chunks on file")

        if args.dry_run:
            if not chunks:
                _die("no chunks to send")
            c = chunks[0]
            block = Block(paragraph_id=(c.paragraph_ids or [c.chunk_hash])[0],
                          ordinal=c.ordinal, text=c.text,
                          page_number=c.page_number)
            params = request_params([block])
            print(f"\n  model:      {params['model']}")
            print(f"  max_tokens: {params['max_tokens']}")
            print(f"  system:     {len(params['system'][0]['text'])} chars, "
                  f"cache_control={params['system'][0]['cache_control']}")
            print(f"  schema:     {len(json.dumps(params['output_config']['format']['schema']))} chars")
            print(f"\n  user message:\n{params['messages'][0]['content'][:600]}")
            return 0

        if not (os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            _die("no Anthropic credentials. Run `ant auth login`, or export "
                 "ANTHROPIC_API_KEY. (`--dry-run` needs neither.)")

        if args.limit:
            print(f"  limiting to {args.limit} chunks")

        from workers.memory_builder import build_for_document

        stats = build_for_document(db, company_id=company.id, document=document,
                                   limit=args.limit)

    d = stats.as_dict()
    print("\n  --- result ---")
    for key in ("entities", "relationships", "mentions_new", "mentions_seen",
                "metrics", "dropped_uncited", "responses_rejected",
                "type_conflicts"):
        print(f"  {key:20} {d.get(key, 0)}")
    if d.get("by_type"):
        print("\n  by type:")
        for k, v in sorted(d["by_type"].items(), key=lambda kv: -kv[1]):
            print(f"    {k:12} {v}")

    kept = d.get("entities", 0) + d.get("relationships", 0)
    dropped = d.get("dropped_uncited", 0)
    total = kept + dropped
    if total:
        print(f"\n  drop rate: {dropped / total:.1%} "
              f"({dropped} dropped of {total})")
    if d.get("responses_rejected"):
        print(f"  {d['responses_rejected']} response(s) rejected outright — "
              "check the log above for the reason")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
