#!/usr/bin/env python
"""Measure extraction quality against real filing text.

Runs Claude over one real 10-K section, stores every raw response, stores the
validated entities, and writes a report. This is the number the next prompt
change gets compared against.

    export ANTHROPIC_API_KEY=...
    export DATABASE_URL=postgresql+psycopg://localhost/evident
    python tools/benchmark.py --seed                    # ~14 chunks of Item 1A
    python tools/benchmark.py --report-only             # re-read the last run

It drives `build_for_document` — the same function the worker runs — with a
recorder attached, so a green benchmark says the pipeline works rather than
that a benchmark script works.

The corpus is `tests/fixtures/edgar-bench/`: verbatim Risk Factors paragraphs
from NVIDIA's FY2025 10-K. Changing it invalidates comparison with earlier
runs.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import os
import socketserver
import statistics
import sys
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "edgar-bench"
REPORTS = ROOT / "docs" / "benchmarks"

PROVIDER = "anthropic"

#: Claude Opus 5, $ per million tokens. Cache reads are ~10% of input; cache
#: writes ~125%. Approximate by design — it exists to answer "what would a full
#: filing cost", not to reconcile an invoice.
PRICE_IN, PRICE_OUT = Decimal("5.00"), Decimal("25.00")
CACHE_READ_MULT, CACHE_WRITE_MULT = Decimal("0.1"), Decimal("1.25")


def compute_cost(input_tokens: int, output_tokens: int,
                 cache_read: int, cache_created: int) -> Decimal:
    """Decimal throughout: this lands in a NUMERIC column and must add up."""
    billed_in = (Decimal(input_tokens)
                 + Decimal(cache_read) * CACHE_READ_MULT
                 + Decimal(cache_created) * CACHE_WRITE_MULT)
    return ((billed_in / Decimal(1_000_000) * PRICE_IN
             + Decimal(output_tokens) / Decimal(1_000_000) * PRICE_OUT)
            .quantize(Decimal("0.000001")))


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


@contextlib.contextmanager
def _fixture_origin(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(directory))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        prev = {k: os.environ.get(k) for k in
                ("SEC_WWW_URL", "SEC_DATA_URL", "SEC_ARCHIVES_URL", "SEC_USER_AGENT")}
        os.environ.update({"SEC_WWW_URL": base, "SEC_DATA_URL": base,
                           "SEC_ARCHIVES_URL": f"{base}/Archives/edgar/data",
                           "SEC_USER_AGENT": "Evident benchmark"})
        import evident_parser.edgar as edgar
        edgar._TICKER_MAP = None
        edgar.BASE = edgar.WWW = base
        edgar.ARCHIVES = f"{base}/Archives/edgar/data"
        try:
            yield base
        finally:
            httpd.shutdown()
            for k, v in prev.items():
                os.environ.pop(k, None) if v is None else os.environ.update({k: v})


def _pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "—"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap chunks (default: the whole section)")
    ap.add_argument("--seed", action="store_true",
                    help="ingest the benchmark corpus first")
    ap.add_argument("--run-id", default=None, help="UUID of a run to re-report")
    ap.add_argument("--report-only", action="store_true",
                    help="re-render the report for the newest run, no API calls")
    ap.add_argument("--out", default=None, help="report path (default docs/benchmarks/)")
    args = ap.parse_args()

    from sqlalchemy import desc, func, select

    from evident_ai.extract import Extraction, ExtractionRejected
    from evident_ai.prompts import EXTRACT_ENTITIES, MODEL
    from evident_db import (Chunk, Company, Document, Entity, EntityMention,
                            ExtractionCall, ExtractionRun, Relationship,
                            session_scope)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _die("DATABASE_URL is not set.")
    ticker = args.ticker.upper()

    # -------------------------------------------------------------- seed
    if args.seed:
        from workers.ingest_worker import ingest_ticker
        with _fixture_origin(FIXTURE):
            f = ingest_ticker(ticker, limit=1, url=dsn).filings[0]
        print(f"  corpus: {f.form_type} {f.accession} — {f.chunks} chunks, "
              f"{f.pages} pages")

    run_id = uuid.UUID(args.run_id) if args.run_id else uuid.uuid4()

    # --------------------------------------------------------------- run
    if not args.report_only:
        if not (os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            _die("no Anthropic credentials. Run `ant auth login`, or export "
                 "ANTHROPIC_API_KEY. (--report-only needs neither.)")

        from workers.memory_builder import build_for_document

        with session_scope(dsn) as db:
            company = db.execute(select(Company).where(
                Company.ticker == ticker)).scalars().first()
            if company is None:
                _die(f"no company {ticker!r}. Re-run with --seed.")
            document = db.execute(
                select(Document).where(Document.company_id == company.id)
                .order_by(desc(Document.filed_at))).scalars().first()
            if document is None:
                _die(f"no documents for {ticker}")

            by_paragraph = {(c.paragraph_ids or [c.chunk_hash])[0]: c
                            for c in db.execute(select(Chunk).where(
                                Chunk.document_id == document.id)).scalars()}

            document_id, company_id = document.id, company.id

        # Committed in its own transaction, before the first request. Written
        # inside the extraction transaction it would roll back with everything
        # else, and a run that died would leave no trace at all — which is the
        # difference between "it failed" and "it was never attempted". A null
        # finished_at is the marker for the former.
        with session_scope(dsn) as db:
            db.add(ExtractionRun(
                id=run_id, provider=PROVIDER, model=MODEL,
                prompt_id=EXTRACT_ENTITIES.id, document_id=document_id,
                started_at=datetime.now(timezone.utc)))

        with session_scope(dsn) as db:
            document = db.get(Document, document_id)
            company = db.get(Company, company_id)
            run = db.get(ExtractionRun, run_id)
            by_paragraph = {(c.paragraph_ids or [c.chunk_hash])[0]: c
                            for c in db.execute(select(Chunk).where(
                                Chunk.document_id == document_id)).scalars()}

            rows: list[ExtractionCall] = []

            def record(key: str, result) -> None:
                """Every call, accepted or not — a failed response is evidence."""
                chunk = by_paragraph.get(key)
                rejected = isinstance(result, ExtractionRejected)
                u = result.usage
                rows.append(ExtractionCall(
                    run_id=run_id, company_id=company.id, document_id=document.id,
                    chunk_id=chunk.id if chunk else None,
                    prompt_id=EXTRACT_ENTITIES.id, model=MODEL,
                    status="rejected" if rejected else "accepted",
                    rejection_reason=result.reason if rejected else None,
                    stop_reason=(result.stop_reason if not rejected
                                 else result.stop_reason),
                    raw_response=result.raw,
                    input_tokens=u.input_tokens if u else 0,
                    output_tokens=u.output_tokens if u else 0,
                    cache_read_tokens=u.cache_read if u else 0,
                    cache_created_tokens=u.cache_created if u else 0,
                    latency_ms=result.latency_ms,
                    entities_returned=0 if rejected else result.entities_returned,
                    entities_kept=0 if rejected else len(result.entities),
                    relationships_returned=(0 if rejected
                                            else result.relationships_returned),
                    relationships_kept=0 if rejected else len(result.relationships),
                ))

            print(f"  run {run_id}: extracting "
                  f"{args.limit or len(by_paragraph)} chunk(s) with "
                  f"{EXTRACT_ENTITIES.id} on {MODEL}")
            build_for_document(db, company_id=company.id, document=document,
                               limit=args.limit, on_result=record)
            db.add_all(rows)

            run.chunks_processed = len(rows)
            run.chunks_accepted = sum(r.status == "accepted" for r in rows)
            run.chunks_rejected = sum(r.status == "rejected" for r in rows)
            run.input_tokens = sum(r.input_tokens for r in rows)
            run.output_tokens = sum(r.output_tokens for r in rows)
            run.cached_input_tokens = sum(r.cache_read_tokens for r in rows)
            run.cost_usd = compute_cost(
                run.input_tokens, run.output_tokens, run.cached_input_tokens,
                sum(r.cache_created_tokens for r in rows))
            run.finished_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------ report
    with session_scope(dsn) as db:
        if args.report_only and not args.run_id:
            newest = db.execute(select(ExtractionRun)
                                .order_by(desc(ExtractionRun.started_at))
                                .limit(1)).scalars().first()
            if newest is None:
                _die("no benchmark runs stored yet")
            run_id = newest.id

        run = db.get(ExtractionRun, run_id)
        if run is None:
            _die(f"no run {run_id}")
        runs = list(db.execute(select(ExtractionCall)
                               .where(ExtractionCall.run_id == run_id)
                               .order_by(ExtractionCall.id)).scalars())
        if not runs:
            _die(f"no calls recorded for run {run_id}")

        entities = list(db.execute(select(Entity)).scalars())
        edges = list(db.execute(select(Relationship)).scalars())
        mentions = db.execute(select(func.count(EntityMention.id))).scalar_one()
        confidences = [m.confidence for m in
                       db.execute(select(EntityMention)).scalars()
                       if m.confidence is not None]
        by_id = {e.id: e for e in entities}
        edge_rows = [(by_id[e.source_entity_id].name, e.relationship_type,
                      by_id[e.target_entity_id].name, e.strength)
                     for e in edges
                     if e.source_entity_id in by_id and e.target_entity_id in by_id]

    accepted = [r for r in runs if r.status == "accepted"]
    rejected = [r for r in runs if r.status == "rejected"]
    ent_ret = sum(r.entities_returned for r in runs)
    ent_kept = sum(r.entities_kept for r in runs)
    rel_ret = sum(r.relationships_returned for r in runs)
    rel_kept = sum(r.relationships_kept for r in runs)
    tok_in = sum(r.input_tokens for r in runs)
    tok_out = sum(r.output_tokens for r in runs)
    c_read = sum(r.cache_read_tokens for r in runs)
    c_made = sum(r.cache_created_tokens for r in runs)
    lat = [r.latency_ms for r in runs if r.latency_ms is not None]
    cost = run.cost_usd if run.cost_usd else compute_cost(tok_in, tok_out,
                                                          c_read, c_made)

    by_type: dict[str, int] = {}
    for e in entities:
        by_type[e.entity_type] = by_type.get(e.entity_type, 0) + 1

    L: list[str] = []
    L.append(f"# Extraction benchmark — {run_id}\n")
    L.append(f"- corpus: `tests/fixtures/edgar-bench/` (NVIDIA FY2025 10-K, Item 1A)")
    L.append(f"- provider: `{run.provider}`  ·  model: `{run.model}`  ·  "
             f"prompt: `{run.prompt_id}`")
    L.append(f"- chunks: {run.chunks_processed}"
             + (f"  ·  {run.duration_s:.1f}s" if run.duration_s is not None
                else "  ·  **did not finish**"))
    L.append(f"- started: {run.started_at:%Y-%m-%d %H:%M:%S %Z}\n")

    L.append("## Acceptance\n")
    L.append("| | returned | kept | rate |")
    L.append("|---|---|---|---|")
    L.append(f"| responses | {len(runs)} | {len(accepted)} | "
             f"{_pct(len(accepted), len(runs))} |")
    L.append(f"| entities | {ent_ret} | {ent_kept} | {_pct(ent_kept, ent_ret)} |")
    L.append(f"| relationships | {rel_ret} | {rel_kept} | {_pct(rel_kept, rel_ret)} |")
    L.append("")
    if rejected:
        L.append("Rejected responses:\n")
        for r in rejected:
            L.append(f"- chunk `{r.chunk_id}` — {r.rejection_reason}")
        L.append("")
    else:
        L.append("No response was rejected: every one parsed as "
                 "`EntityExtractionResponse`.\n")

    L.append("## Yield\n")
    L.append(f"- entities per chunk: **{ent_kept / len(runs):.1f}**")
    L.append(f"- relationships per chunk: **{rel_kept / len(runs):.1f}**")
    L.append(f"- distinct entities stored: **{len(entities)}** "
             f"across {mentions} mentions")
    L.append(f"- edges stored: **{len(edges)}**\n")
    if by_type:
        L.append("| type | entities |")
        L.append("|---|---|")
        for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]):
            L.append(f"| {k} | {v} |")
        L.append("")

    if confidences:
        L.append("## Confidence\n")
        L.append(f"- min {min(confidences):.2f} · median "
                 f"{statistics.median(confidences):.2f} · max {max(confidences):.2f}")
        spread = max(confidences) - min(confidences)
        L.append(f"- spread {spread:.2f}"
                 + ("  — a distribution this tight means the model is not "
                    "discriminating between certain and inferred, which is "
                    "worse than a wide spread" if spread < 0.1 else ""))
        L.append("")

    L.append("## Cost and cache\n")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| input tokens | {tok_in:,} |")
    L.append(f"| output tokens | {tok_out:,} |")
    L.append(f"| cache created | {c_made:,} |")
    L.append(f"| cache read | {c_read:,} |")
    L.append(f"| cache hit rate | {_pct(c_read, c_read + tok_in + c_made)} |")
    if lat:
        L.append(f"| median latency | {statistics.median(lat):,.0f} ms |")
    L.append(f"| cost, this run | ${cost:.6f} |")
    L.append(f"| cost per chunk | ${cost / len(runs):.6f} |")
    L.append(f"| projected, 474-chunk 10-K | ${cost / len(runs) * 474:.2f} |")
    L.append("")

    if edge_rows:
        L.append("## Relationships extracted\n")
        for src, kind, dst, strength in edge_rows[:15]:
            L.append(f"- `{src}` —{kind}→ `{dst}` ({strength:.2f})")
        L.append("")

    L.append("## Entities extracted\n")
    for e in sorted(entities, key=lambda e: -e.mention_count)[:30]:
        L.append(f"- **{e.name}** · `{e.entity_type}` · {e.mention_count} mention(s)")
    L.append("")

    L.append("---\n")
    L.append(f"Run `{run_id}` — totals in `extraction_runs`, and the raw "
             "response for every chunk in `extraction_calls` under the same "
             "id, so this run can be replayed against a changed schema without "
             "paying for it again.")

    report = "\n".join(L) + "\n"
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else REPORTS / f"{run_id}.md"
    out.write_text(report, encoding="utf-8")

    print(f"\n  responses  {len(accepted)}/{len(runs)} accepted")
    print(f"  entities   {ent_kept}/{ent_ret} kept ({_pct(ent_kept, ent_ret)})")
    print(f"  edges      {rel_kept}/{rel_ret} kept ({_pct(rel_kept, rel_ret)})")
    print(f"  cache      {_pct(c_read, c_read + tok_in + c_made)} of input read from cache")
    print(f"  cost       ${cost:.6f}  (${cost / len(runs):.6f}/chunk)")
    print(f"\n  report -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
