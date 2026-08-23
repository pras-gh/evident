"""Command line entry point.

    python -m elevate_ingest.cli ingest --cik 320193 --accession 0000320193-25-000073
    python -m elevate_ingest.cli parse  --file filing.htm --accession 0000320193-25-000073
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="elevate-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="fetch a filing from EDGAR and store it")
    ing.add_argument("--cik", required=True)
    ing.add_argument("--accession", required=True)
    ing.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ing.add_argument("--target-tokens", type=int, default=350)
    ing.add_argument("--dry-run", action="store_true",
                     help="parse and chunk, print stats, write nothing")

    par = sub.add_parser("parse", help="parse a local file — no network, no database")
    par.add_argument("--file", required=True)
    par.add_argument("--accession", required=True)
    par.add_argument("--show", type=int, default=5, help="paragraphs to preview")

    args = ap.parse_args(argv)

    if args.cmd == "parse":
        from .chunker import chunk_document
        from .parse_html import parse_html
        html = open(args.file, encoding="utf-8", errors="replace").read()
        sections, blocks, tables, pages = parse_html(html, accession=args.accession)
        chunks = chunk_document(accession=args.accession, blocks=blocks,
                                tables=tables, sections=sections)
        print(json.dumps({"pages": pages, "sections": len(sections),
                          "paragraphs": len(blocks), "tables": len(tables),
                          "chunks": len(chunks)}, indent=2))
        for b in blocks[: args.show]:
            print(f"  {b.paragraph_id} p{b.page_number} {b.text[:80]!r}")
        return 0

    from .pipeline import ingest_accession
    result = ingest_accession(
        cik=args.cik, accession=args.accession,
        dsn=None if args.dry_run else args.dsn,
        target_tokens=args.target_tokens,
    )
    print(json.dumps({"accession": result.accession, "skipped": result.skipped,
                      "document_id": result.document_id, **result.stats}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
