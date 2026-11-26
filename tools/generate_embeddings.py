#!/usr/bin/env python3
"""Embed filing chunks with the configured model, resumably.

    python tools/generate_embeddings.py --provider voyage --dry-run
    python tools/generate_embeddings.py --provider voyage --limit 100
    python tools/generate_embeddings.py --provider voyage --ticker NVDA
    python tools/generate_embeddings.py --provider voyage --force     # re-embed everything in scope

The command line for `workers/embedding_worker.py`, the code the sync worker
and on-demand fetches embed with. Run it again after it stops, for any reason,
and it carries on with what is left: every batch is committed as it lands,
and what is left is read back from the table.

Settings come from the environment, and from `.env` for anything the
environment does not set: EMBEDDING_PROVIDER, EMBEDDING_MODEL, the provider's
key (VOYAGE_API_KEY), DATABASE_URL; VOYAGE_RPM / VOYAGE_TPM to pace requests
under an account's rate limit. Search embeds queries with EMBEDDING_PROVIDER /
EMBEDDING_MODEL, so embedding with anything else warns: search would not read
those vectors.

Exit status: 0 when everything in scope is embedded, 1 when chunks failed or
remain, 2 when the run could not start or stopped early.
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path.insert(0, str(ROOT))


def load_env(path: pathlib.Path = ROOT / ".env") -> None:
    """KEY=value lines, as data (not shell), for keys not already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def verify(url: str | None, provider: str, model: str) -> dict:
    """What PostgreSQL holds for this provider and model: rows, their widths,
    and whether any vector is unusable — checked in the database, not taken
    from the run's own count."""
    from sqlalchemy import text
    from parallx_db import session_scope
    with session_scope(url) as db:
        row = db.execute(text(
            "select count(*), min(vector_dims(embedding)), max(vector_dims(embedding)), "
            "count(*) filter (where vector_norm(embedding) = 'NaN'::float8 "
            "                  or vector_norm(embedding) = 0), "
            "min(vector_norm(embedding)), max(vector_norm(embedding)) "
            "from chunks where embedding is not null "
            "and embedding_provider = :p and embedding_model = :m"),
            {"p": provider, "m": model}).one()
        others = db.execute(text(
            "select coalesce(embedding_provider, '-') || '/' || coalesce(embedding_model, '-'), "
            "count(*) from chunks where embedding is not null "
            "and not (embedding_provider = :p and embedding_model = :m) group by 1"),
            {"p": provider, "m": model}).all()
    return {"rows": row[0], "dims": (row[1], row[2]), "unusable": row[3],
            "norm": (row[4], row[5]), "other_models": dict(others)}


def main(argv: list[str] | None = None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", default=os.environ.get("EMBEDDING_PROVIDER"),
                    help="voyage, openai, gemini (default: EMBEDDING_PROVIDER)")
    ap.add_argument("--model", default=None,
                    help="default: EMBEDDING_MODEL, else the provider's default")
    ap.add_argument("--batch-size", type=int, default=128, help="chunks per request (max 1000)")
    ap.add_argument("--limit", type=int, help="embed at most this many chunks")
    ap.add_argument("--ticker", help="only this company's chunks")
    ap.add_argument("--force", action="store_true",
                    help="re-embed chunks this model already embedded")
    ap.add_argument("--dry-run", action="store_true", help="count, embed nothing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from parallx_retrieval.embed import EmbeddingError, get_provider
    from workers.embedding_worker import EmbeddingStopped, run

    if not args.provider:
        print("No provider: pass --provider or set EMBEDDING_PROVIDER.", file=sys.stderr)
        return 2
    if args.provider.lower() in ("hashing", "local"):
        print("Refusing the hashing provider: it has no semantics, and its vectors "
              "would make search look like it works. It is for tests.", file=sys.stderr)
        return 2
    same_as_env = args.provider == os.environ.get("EMBEDDING_PROVIDER")
    model = args.model or (os.environ.get("EMBEDDING_MODEL") if same_as_env else None)
    try:
        embedder = get_provider(args.provider, model=model)
    except EmbeddingError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL")

    search = (os.environ.get("EMBEDDING_PROVIDER"), os.environ.get("EMBEDDING_MODEL"))
    if (embedder.name, embedder.model) != (search[0], search[1] or embedder.model):
        print(f"warning: search embeds queries with {search[0]}/{search[1] or 'its default'}; "
              f"vectors from {embedder.name}/{embedder.model} will not be searched until "
              "EMBEDDING_PROVIDER / EMBEDDING_MODEL say so.", file=sys.stderr)

    print(f"{embedder.name}/{embedder.model}, {embedder.dim} dimensions"
          + (f", ticker {args.ticker.upper()}" if args.ticker else "")
          + (" — dry run" if args.dry_run else ""))

    def progress(r) -> None:
        print(f"  {r.embedded:>6} of {r.planned} embedded, {r.failed} failed, "
              f"{r.tokens:,} tokens", flush=True)

    stopped = None
    try:
        r = run(url=url, embedder=embedder, batch_size=args.batch_size,
                max_chunks=args.limit, ticker=args.ticker, force=args.force,
                dry_run=args.dry_run, progress=None if args.dry_run else progress)
    except EmbeddingStopped as exc:
        r, stopped = exc.result, str(exc)

    cost = r.cost_usd
    lines = [
        ("Total eligible chunks", f"{r.eligible:,}" + (f" ({r.empty} empty, skipped)"
                                                       if r.empty else "")),
        ("Already embedded", f"{r.already:,}" + (" (re-embedding: --force)" if args.force
                                                 else "")),
        ("Remaining", f"{r.remaining:,}" + (f" (this run: {r.planned:,}, --limit {args.limit})"
                                             if args.limit and r.planned < r.remaining else "")),
    ]
    if not args.dry_run:
        lines += [
            ("Embedded now", f"{r.embedded:,} in {r.batches} requests"),
            ("Failed", f"{r.failed}" + (f" — chunk ids {r.failed_ids[:20]}" if r.failed else "")),
            ("Tokens processed", f"{r.tokens:,}" + ("" if r.tokens_reported
                                                     else " (estimated)")),
            ("API errors", f"{len(r.errors)}"),
            ("Cost", f"${cost:.4f} (list price, estimated)" if cost is not None
             else "no price known for this model"),
            ("Duration", f"{r.seconds:.1f}s"),
        ]
    width = max(len(k) for k, _ in lines)
    for k, v in lines:
        print(f"{k:<{width}}  {v}")
    for e in r.errors[-5:]:
        print(f"  error: {e[:240]}")

    if not args.dry_run:
        v = verify(url, embedder.name, embedder.model)
        print(f"Verified in PostgreSQL: {v['rows']:,} chunks hold {embedder.name}/{embedder.model} "
              f"vectors, {v['dims'][0]}–{v['dims'][1]} dimensions, "
              f"norms {v['norm'][0] or 0:.4f}–{v['norm'][1] or 0:.4f}, "
              f"{v['unusable']} unusable")
        if v["other_models"]:
            print("Other models' vectors still present: " + ", ".join(
                f"{k} ({n})" for k, n in v["other_models"].items()))
    if stopped:
        print(f"Stopped: {stopped}\nRun the same command again to continue.", file=sys.stderr)
        return 2
    return 1 if (not args.dry_run and (r.failed or r.left)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
