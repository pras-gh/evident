#!/usr/bin/env python3
"""Retrieval check: semantic search against a keyword baseline, on real filings.

    python tools/eval_retrieval.py                    # the five research queries
    python tools/eval_retrieval.py --out docs/benchmarks/retrieval-voyage-4-large.md
    python tools/eval_retrieval.py --api http://localhost:8000/v1 -k 5

For each query:

* **semantic** — `POST /v1/search` on the running API: the query embedded by
  the configured model, ranked by pgvector cosine distance within the
  companies it names;
* **keyword** — PostgreSQL full-text search over the same companies' chunks
  (`ts_rank_cd`, any of the query's terms). Parallx has no BM25 or hybrid
  retrieval; this is the nearest keyword ranking, for comparison only.

And for every semantic hit: its citation resolves (`GET /v1/evidence/{id}`
with its paragraph), its chunk's vector is the configured model's at the
column's width — and no test-provider (hashing) vector is in the table.

Relevance is not scored by this tool; it prints what each method returned so
a person can judge, and the report says who did.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path.insert(0, str(ROOT))

QUERIES = [
    "What risks does management identify in its latest annual filing?",
    "How has NVIDIA discussed data centre demand?",
    "What has Microsoft said about AI infrastructure spending?",
    "What changes has Amazon disclosed in AWS strategy?",
    "What has Apple said about capital allocation?",
]


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _get(url: str) -> tuple[int, dict | None]:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def keyword(db, query: str, tickers: list[str] | None, k: int) -> list[dict]:
    """Full-text ranking: any of the query's stemmed terms, ts_rank_cd."""
    from sqlalchemy import text
    sql = """
        with q as (
          select to_tsquery('english', coalesce(nullif(array_to_string(
                   tsvector_to_array(to_tsvector('english', :q)), ' | '), ''), 'nothing')) as tq)
        select k.id, c.ticker, d.accession, k.page_number, k.section_title,
               ts_rank_cd(to_tsvector('english', k.text), q.tq) as rank, k.text
        from chunks k join documents d on d.id = k.document_id
        join companies c on c.id = d.company_id, q
        where to_tsvector('english', k.text) @@ q.tq
          and (cast(:tickers as text[]) is null or c.ticker = any(cast(:tickers as text[])))
        order by rank desc limit :k"""
    rows = db.execute(text(sql), {"q": query, "tickers": tickers, "k": k}).all()
    return [{"chunk_id": r[0], "ticker": r[1], "accession": r[2], "page": r[3],
             "section": r[4], "score": float(r[5]), "text": r[6]} for r in rows]


def checks(db, chunk_ids: list[int]) -> dict:
    from sqlalchemy import text
    rows = db.execute(text(
        "select embedding_provider, embedding_model, vector_dims(embedding) from chunks "
        "where id = any(:ids)"), {"ids": chunk_ids}).all() if chunk_ids else []
    hashing = db.execute(text(
        "select count(*) from chunks where embedding_provider in ('local', 'hashing') "
        "or embedding_model like 'hashing%'")).scalar_one()
    return {"models": sorted({f"{p}/{m}" for p, m, _ in rows}),
            "dims": sorted({d for _, _, d in rows}), "hashing_vectors": hashing}


def main() -> int:
    from tools.generate_embeddings import load_env
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://localhost:8000/v1")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()

    from parallx_db import session_scope
    lines = ["# Retrieval: semantic (Voyage) against keyword (full-text)", ""]
    summary = []
    with session_scope(os.environ.get("DATABASE_URL")) as db:
        for q in QUERIES:
            body = _post(f"{args.api}/search", {"query": q, "k": args.k})
            hits = body["hits"][:args.k]
            tickers = (body.get("scope") or {}).get("tickers")
            kw = keyword(db, body.get("topic") or q, tickers, args.k)
            resolved = 0
            for h in hits:
                pid = (h["paragraph_ids"] or [None])[0]
                status, ev = _get(f"{args.api}/evidence/{h['chunk_id']}"
                                  + (f"?paragraph_id={urllib.parse.quote(pid)}" if pid else ""))
                ok = (status == 200 and ev and ev["accession"] == h["accession"]
                      and (pid is None or ev["paragraph_id"] == pid))
                h["resolved"] = bool(ok)
                resolved += bool(ok)
            c = checks(db, [h["chunk_id"] for h in hits])
            from sqlalchemy import text as sql
            owner = dict(db.execute(sql(
                "select d.accession, c.ticker from documents d join companies c "
                "on c.id = d.company_id")).all())
            for h in hits:
                h["ticker"] = owner.get(h["accession"])
            overlap = len({h["chunk_id"] for h in hits} & {r["chunk_id"] for r in kw})
            summary.append({"query": q, "scope": tickers, "embedder": body.get("embedder"),
                            "hits": len(hits), "resolved": resolved, "keyword_hits": len(kw),
                            "overlap": overlap, **c})
            lines += [f"## {q}", "",
                      f"Scope: {', '.join(tickers) if tickers else 'every company'} · "
                      f"embedder {body.get('embedder')} · citations resolved "
                      f"{resolved}/{len(hits)} · vectors {', '.join(c['models']) or '—'} at "
                      f"{c['dims']} dimensions · shared with keyword top {args.k}: {overlap}", "",
                      "| # | semantic (cosine) | keyword (full-text) |", "| --- | --- | --- |"]
            for i in range(max(len(hits), len(kw))):
                def cell(r, score):
                    if r is None:
                        return "—"
                    snippet = " ".join(r["text"].split())[:150].replace("|", "/")
                    where = r.get("citation") or f"p. {r.get('page')} · {r.get('section')}"
                    return (f"**{r.get('ticker') or r['accession']}** {score:.3f} · "
                            f"{where}<br>{snippet}…")
                s = hits[i] if i < len(hits) else None
                k = kw[i] if i < len(kw) else None
                lines.append(f"| {i + 1} | {cell(s, s['score']) if s else '—'} | "
                             f"{cell(k, k['score']) if k else '—'} |")
            lines.append("")
    print(json.dumps(summary, indent=2))
    if args.out:
        args.out.write_text("\n".join(lines) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
