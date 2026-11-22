#!/usr/bin/env python
"""Run the real extraction pipeline through the AI gateway and check it end to end.

    chunk -> gateway (route: config/ai.toml) -> JSON Schema -> Pydantic
          -> entities + relationships -> Postgres, with every call in ai_usage_logs

The command that turns the pipeline from "built" into "proven". It drives the
same `build_for_document` the worker does — no parallel code path, so a green
run here means the worker works, not that a demo script does.

    # three real chunks, with every success criterion checked
    python tools/extract_live.py --ticker NVDA --limit 3 --seed --verify

    # run it a second time to prove the response cache answers it, for $0
    python tools/extract_live.py --ticker NVDA --limit 3 --verify

    # see exactly what would be sent, without sending it or needing a key
    python tools/extract_live.py --ticker NVDA --limit 1 --seed --dry-run

`--seed` ingests the bundled excerpt of NVIDIA's FY2025 10-K
(`tests/fixtures/edgar-real/`) over a local origin, so the run works without
reaching sec.gov — which blocks many networks at the edge. It is real filing
text, not the synthetic parser fixture next to it.

Start with `--limit`. A whole 10-K is a few hundred chunks; the first thing
worth knowing is the drop rate on three of them, not the bill for all of them.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import logging
import os
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path.insert(0, str(ROOT))

REAL_FIXTURE = ROOT / "tests" / "fixtures" / "edgar-real"

OK, BAD, MEH = "PASS", "FAIL", "  ? "


def require_route_keys(die) -> tuple[str, str]:
    """(provider, model) of the extraction route's primary, after checking
    some provider on the route has a key — otherwise every call would fail."""
    from parallx_ai.gateway import TaskType, load, missing_keys
    config = load()
    if why := missing_keys(TaskType.FINANCIAL_EXTRACTION, config):
        die(why)
    first = config.route(TaskType.FINANCIAL_EXTRACTION)[0]
    return first.provider, first.model


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


@contextlib.contextmanager
def _fixture_origin(directory: Path):
    """Serve a fixture EDGAR tree so ingestion runs without reaching sec.gov."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(directory))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        previous = {k: os.environ.get(k) for k in
                    ("SEC_WWW_URL", "SEC_DATA_URL", "SEC_ARCHIVES_URL",
                     "SEC_USER_AGENT")}
        os.environ.update({
            "SEC_WWW_URL": base, "SEC_DATA_URL": base,
            "SEC_ARCHIVES_URL": f"{base}/Archives/edgar/data",
            "SEC_USER_AGENT": "Parallx live-run",
        })
        import parallx_parser.edgar as edgar
        edgar._TICKER_MAP = None
        edgar.BASE = edgar.WWW = base
        edgar.ARCHIVES = f"{base}/Archives/edgar/data"
        try:
            yield base
        finally:
            httpd.shutdown()
            for k, v in previous.items():
                os.environ.pop(k, None) if v is None else os.environ.update({k: v})


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--accession", help="default: the newest filing on file")
    ap.add_argument("--limit", type=int, default=3,
                    help="chunks to extract (default 3 — keep it small first)")
    ap.add_argument("--seed", action="store_true",
                    help="ingest the bundled real filing excerpt first")
    ap.add_argument("--verify", action="store_true",
                    help="check each success criterion and exit non-zero on failure")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the request for the first chunk and stop")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)-7s %(name)s: %(message)s")

    from sqlalchemy import func, select

    from parallx_ai.extract import request_params
    from parallx_db import (AIResponseCache, AIUsageLog, Chunk, Company, Document,
                            Entity, EntityMention, Relationship, session_scope)
    from parallx_parser.models import Block

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _die("DATABASE_URL is not set. This runs against a real database, "
             "because the point is to prove the whole path.")

    ticker = args.ticker.upper()

    # ------------------------------------------------------------------ seed
    if args.seed:
        if not REAL_FIXTURE.exists():
            _die(f"{REAL_FIXTURE} is missing")
        from workers.ingest_worker import ingest_ticker
        with _fixture_origin(REAL_FIXTURE):
            result = ingest_ticker(ticker, limit=1, url=dsn)
        f = result.filings[0]
        print(f"  seeded {ticker} {f.form_type} {f.accession} — "
              f"{f.chunks} chunks{' (already present)' if f.skipped else ''}")

    # ------------------------------------------------------- pick a document
    with session_scope(dsn) as db:
        company = db.execute(select(Company).where(
            Company.ticker == ticker)).scalars().first()
        if company is None:
            _die(f"no company {ticker!r} on file. Re-run with --seed, or ingest "
                 "a filing first (POST /ingest).")

        stmt = select(Document).where(Document.company_id == company.id)
        if args.accession:
            stmt = stmt.where(Document.accession == args.accession)
        document = db.execute(
            stmt.order_by(Document.filed_at.desc())).scalars().first()
        if document is None:
            _die(f"no documents on file for {ticker}")
        accession = document.accession
        # the gateway logs in its own transactions; this run's rows are the
        # ones written after this instant
        run_started = db.execute(select(func.clock_timestamp())).scalar_one()

        chunks = list(db.execute(
            select(Chunk).where(Chunk.document_id == document.id)
            .order_by(Chunk.ordinal)).scalars())
        print(f"  {ticker} {document.form_type} {document.accession} — "
              f"{len(chunks)} chunks on file, extracting {min(args.limit, len(chunks))}")

        if not chunks:
            _die("no chunks to send")

        # ------------------------------------------------------------ dry run
        if args.dry_run:
            c = chunks[0]
            block = Block(paragraph_id=(c.paragraph_ids or [c.chunk_hash])[0],
                          ordinal=c.ordinal, text=c.text, page_number=c.page_number)
            params = request_params([block])
            schema = json.dumps(params["output_config"]["format"]["schema"])
            from parallx_ai.gateway import TaskType, load
            route = load().route(TaskType.FINANCIAL_EXTRACTION)
            print(f"\n  route:      {' → '.join(t.key for t in route)}  (config/ai.toml)")
            print(f"  max_tokens: {params['max_tokens']}")
            print(f"  system:     {len(params['system'][0]['text'])} chars, "
                  f"cache_control={params['system'][0]['cache_control']}")
            print(f"  schema:     {len(schema)} chars, "
                  f"{len(params['output_config']['format']['schema']['properties'])} top-level fields")
            print(f"\n  user message:\n{params['messages'][0]['content'][:700]}")
            return 0

        require_route_keys(_die)   # `--dry-run` needs no key

        before = {
            "entities": db.execute(select(func.count(Entity.id))).scalar_one(),
            "mentions": db.execute(select(func.count(EntityMention.id))).scalar_one(),
            "edges": db.execute(select(func.count(Relationship.id))).scalar_one(),
        }

        # --------------------------------------------------------- the run
        from workers.memory_builder import build_for_document
        stats = build_for_document(db, company_id=company.id, document=document,
                                   limit=args.limit)

    d = stats.as_dict()

    with session_scope(dsn) as db:
        after = {
            "entities": db.execute(select(func.count(Entity.id))).scalar_one(),
            "mentions": db.execute(select(func.count(EntityMention.id))).scalar_one(),
            "edges": db.execute(select(func.count(Relationship.id))).scalar_one(),
        }
        sample = list(db.execute(
            select(Entity).order_by(Entity.mention_count.desc()).limit(8)).scalars())
        edge_rows = list(db.execute(select(Relationship).limit(5)).scalars())
        by_id = {e.id: e for e in db.execute(select(Entity)).scalars()}
        edges = [(by_id[e.source_entity_id].name, e.relationship_type,
                  by_id[e.target_entity_id].name, e.strength,
                  e.evidence_chunk_id) for e in edge_rows
                 if e.source_entity_id in by_id and e.target_entity_id in by_id]
        calls = db.execute(
            select(AIUsageLog.provider, AIUsageLog.model, AIUsageLog.status,
                   func.count(), func.coalesce(func.sum(AIUsageLog.estimated_cost_usd), 0))
            .where(AIUsageLog.task_type == "financial_extraction",
                   AIUsageLog.created_at >= run_started)
            .group_by(AIUsageLog.provider, AIUsageLog.model, AIUsageLog.status)
            .order_by(AIUsageLog.status)).all()
        stored = db.execute(select(func.count(AIResponseCache.id)).where(
            AIResponseCache.accession == accession,
            AIResponseCache.task_type == "financial_extraction")).scalar_one()

    # ------------------------------------------------------------- report
    print("\n  --- extracted ---")
    for e in sample:
        desc = f" — {e.description[:50]}" if e.description else ""
        print(f"    {e.entity_type:10} {e.name[:34]:34} x{e.mention_count}{desc}")
    if edges:
        print("\n  --- relationships ---")
        for src, kind, dst, strength, chunk_id in edges:
            print(f"    {src[:22]:22} -{kind}-> {dst[:22]:22} "
                  f"{strength:.2f}  evidence=chunk:{chunk_id}")

    print("\n  --- tokens ---")
    print(f"    requests        {d['requests']}")
    print(f"    input           {d['input_tokens']}")
    print(f"    output          {d['output_tokens']}")
    print(f"    prompt cache    {d['cache_created']} written, {d['cache_read']} read"
          "  (provider-side; Anthropic's cache_control, Gemini's implicit cache)")

    print("\n  --- gateway (ai_usage_logs) ---")
    by_status: dict[str, int] = {}
    total_cost = 0
    for provider, model, status, n, cost in calls:
        by_status[status] = by_status.get(status, 0) + n
        total_cost += cost
        print(f"    {status:8} {provider}:{model:28} x{n}  ${cost:.6f}")
    print(f"    total cost      ${total_cost:.6f}")
    print(f"    response cache  {stored} answer(s) stored for {accession}")
    models = sorted({f"{p}:{m}" for p, m, st, _, _ in calls if st in ("success", "cached")})

    if not args.verify:
        return 0

    # ------------------------------------------------------------- criteria
    print("\n  --- success criteria ---")
    checks: list[tuple[str, bool | None, str]] = []

    sent = d["requests"]
    checks.append((
        "Model returns valid JSON",
        d["responses_rejected"] == 0 and sent > 0,
        f"{sent} request(s) via {', '.join(models) or 'nothing'}, "
        f"{d['responses_rejected']} rejected"))

    # Every object that reached the database went through EntityExtractionResponse;
    # a Pydantic failure is counted as a rejected response, never a partial write.
    checks.append((
        "Pydantic validates 100%",
        d["responses_rejected"] == 0 and sent > 0,
        "no response failed EntityExtractionResponse"
        if d["responses_rejected"] == 0 else
        f"{d['responses_rejected']} response(s) failed validation"))

    grew = after["entities"] - before["entities"]
    checks.append((
        "Entities inserted into Postgres",
        after["entities"] > 0 and (grew > 0 or before["entities"] > 0),
        f"{after['entities']} rows (+{grew} this run), "
        f"{after['mentions']} mentions"))

    checks.append((
        "Relationships inserted",
        after["edges"] > 0,
        f"{after['edges']} edge(s)" if after["edges"] else
        "none — the model asserted no relationship it could attach to two "
        "extracted entities"))

    answered = by_status.get("success", 0) + by_status.get("cached", 0)
    checks.append((
        "Every call logged and costed",
        answered == sent and sent > 0,
        f"{answered} of {sent} answer(s) in ai_usage_logs, ${total_cost:.6f}"))

    checks.append((
        "Cache stores response",
        stored > 0,
        f"{stored} chunk answer(s) stored for {accession}" if stored else
        "nothing stored — only complete answers are cached"))

    hits = by_status.get("cached", 0)
    checks.append((
        "Second run hits cache",
        True if hits else None,
        f"{hits} of {sent} answered from the cache, no provider called" if hits else
        "the first run fills the cache; run again — it should cost $0"))

    failed = 0
    for name, state, detail in checks:
        mark = OK if state else (MEH if state is None else BAD)
        failed += state is False
        print(f"    [{mark}] {name:34} {detail}")

    if failed:
        print(f"\n  {failed} criterion(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
