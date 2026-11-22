#!/usr/bin/env python
"""Seed a local database so the evidence viewer has something real to show.

    DATABASE_URL=... python tools/seed_demo.py                     # one filing
    DATABASE_URL=... python tools/seed_demo.py --corpus timeline   # three years
    open http://localhost:3000/evidence/1?entity=export_controls
    open http://localhost:3000/timeline/nvda

    # real extraction through the AI gateway, replacing whatever NVDA had
    DATABASE_URL=... GEMINI_API_KEY=... python tools/seed_demo.py \
        --corpus timeline --extract gateway --replace

**By default this is not a model.** Entities are found by a keyword matcher, so
the demo runs without an API key. What it produces is still truthful — every
citation points at a paragraph that really contains the named thing, and every
quote is a real sentence from that paragraph — but it finds only the handful of
names in `VOCABULARY`, and it has no judgment about what a filing is saying.

`--extract gateway` sends the same chunks through the AI gateway's
`financial_extraction` route instead (config/ai.toml; Gemini Flash-Lite by
default). That is paid — about $0.002 a chunk, so roughly $0.20 for the
timeline corpus's 94 — logged to `ai_usage_logs` against the internal
organization, and cached by filing, so seeding again costs nothing.
`--replace` deletes NVDA's memory first, so it comes from one extractor rather
than a mix of the two.

Either way it drives the same `build_for_document` the worker runs, through the
same Pydantic gate and citation guard. So the viewer is exercised against the
real storage path, not a hand-built fixture.

Two corpora, both verbatim Risk Factors from NVIDIA 10-Ks, ingested over a
local origin so sec.gov is never contacted:

    bench     FY2025 only — the extraction benchmark corpus
    timeline  FY2024, FY2025 and FY2026 — real change for the timeline to find
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

CORPORA = {
    # (fixture root, filings to ingest)
    "bench": (ROOT / "tests" / "fixtures" / "edgar-bench", 1),
    "timeline": (ROOT / "tests" / "fixtures" / "edgar-timeline", 3),
}

#: (pattern, canonical name, entity type). Deliberately small and literal.
VOCABULARY = [
    (r"\bexport (?:control|restriction)s?\b|\blicensing requirements?\b",
     "Export Controls", "risk"),
    (r"\bChina\b", "China", "geography"),
    (r"\bTaiwan\b", "Taiwan", "geography"),
    (r"\bIsrael\b", "Israel", "geography"),
    (r"\bRussia\b", "Russia", "geography"),
    (r"\bBlackwell\b", "Blackwell", "product"),
    (r"\bDGX Cloud\b", "DGX Cloud", "product"),
    (r"\bsupply chain\b", "Supply Chain", "risk"),
    (r"\bAI Diffusion\b", "AI Diffusion Rule", "risk"),
    (r"\bcyber-?attacks?\b|\bransomware\b", "Cybersecurity", "risk"),
    (r"\bclimate\b", "Climate Regulation", "risk"),
    (r"\btariffs?\b", "Tariffs", "risk"),
    (r"\bH20\b", "H20", "product"),
]

#: Sentence ends, except after the abbreviations filings are full of — a
#: quote starting "and foreign government bodies" because "U.S." ended a
#: sentence reads as a broken citation.
_SENTENCE = re.compile(r"(?<=[.!?])(?<!U\.S\.)(?<!\bInc\.)(?<!\bNo\.)"
                       r"(?<!\be\.g\.)(?<!\bi\.e\.)\s+")


def _looks_like_heading(text: str) -> bool:
    """Subsection titles parse as paragraphs; they name a topic, they do not
    say anything about it, so they make poor evidence."""
    return len(text.split()) < 12 and not text.rstrip().endswith((".", "?", "!"))


class KeywordClient:
    """Answers extraction requests the way the API would, from keywords.

    Reads the numbered paragraphs out of the request exactly as the model
    receives them, and cites the paragraph each match was found in — so the
    citation guard downstream checks real ids, not invented ones.
    """

    @property
    def messages(self):
        client = self

        class _Messages:
            def create(self, **request):
                return client._respond(request["messages"][0]["content"])

        return _Messages()

    @staticmethod
    def _respond(body: str):
        entities = []
        for part in body.split("\n\n"):
            if not part.startswith("[") or "] " not in part:
                continue
            pid, text = part[1:].split("] ", 1)
            if _looks_like_heading(text):
                continue
            for pattern, name, kind in VOCABULARY:
                rx = re.compile(pattern, re.I)
                if not rx.search(text):
                    continue
                sentence = next((s for s in _SENTENCE.split(text) if rx.search(s)), text)
                entities.append({
                    "name": name, "entity_type": kind,
                    # an exact literal match states the thing outright, which
                    # is what the prompt defines 1.0 to mean
                    "confidence": 1.0,
                    "paragraph_id": pid, "quote": sentence[:400],
                })
        payload = json.dumps({"entities": entities, "relationships": []})
        text_block = type("Text", (), {"type": "text", "text": payload})()
        usage = type("Usage", (), {"input_tokens": 0, "output_tokens": 0,
                                   "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0})()
        return type("Response", (), {"content": [text_block], "usage": usage,
                                     "stop_reason": "end_turn"})()


def clear_memory(db, company_id: int) -> int:
    """Delete a company's entities, and by cascade their mentions,
    relationships and metric observations. Filings, chunks and rendered pages
    stay, so nothing is ingested or rendered again. Returns entities deleted."""
    from sqlalchemy import delete
    from parallx_db import Entity
    return db.execute(delete(Entity).where(Entity.company_id == company_id)).rowcount


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", choices=sorted(CORPORA), default="bench")
    ap.add_argument("--extract", choices=("keywords", "gateway"), default="keywords",
                    help="keywords: the matcher here, no key needed (default). "
                         "gateway: real extraction on the financial_extraction route; paid")
    ap.add_argument("--replace", action="store_true",
                    help="delete NVDA's memory first, so it comes from one extractor")
    args = ap.parse_args(argv)
    corpus, filings = CORPORA[args.corpus]

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: set DATABASE_URL (a migrated database)", file=sys.stderr)
        return 2

    if args.extract == "gateway":
        from parallx_ai.gateway import TaskType, missing_keys
        if why := missing_keys(TaskType.FINANCIAL_EXTRACTION):
            print(f"error: {why}", file=sys.stderr)
            return 2

    from sqlalchemy import func, select

    from parallx_ai.gateway import UsageLimitExceeded
    from parallx_db import Company, Document, Entity, EntityMention, session_scope
    from test_ingest_e2e import fixture_origin
    from workers.ingest_worker import ingest_ticker
    from workers.memory_builder import build_for_document

    with fixture_origin(corpus):
        ingested = ingest_ticker("NVDA", form_types=["10-K"], limit=filings,
                                 url=dsn).filings

    if args.replace:
        with session_scope(dsn) as db:
            company = db.execute(select(Company).where(Company.ticker == "NVDA")).scalar_one()
            print(f"  replaced: deleted {clear_memory(db, company.id)} NVDA entities "
                  "and their mentions and relationships")

    spent = Decimal(0)

    def tally(_key, result):
        nonlocal spent
        spent += getattr(result, "cost_usd", None) or 0

    # oldest first, so first_seen and latest_seen advance the way they would
    # if each filing had been processed the day it was published
    for filing in sorted(ingested, key=lambda f: f.filed_at):
        print(f"  {filing.form_type} {filing.accession}: "
              + ("already ingested, unchanged" if filing.skipped else f"{filing.chunks} chunks"))
        client = KeywordClient() if args.extract == "keywords" else None
        try:
            # a limit raised inside the session rolls this filing back whole
            with session_scope(dsn) as db:
                company = db.execute(select(Company).where(Company.ticker == "NVDA")).scalar_one()
                document = db.execute(select(Document).where(
                    Document.accession == filing.accession)).scalar_one()
                stats = build_for_document(db, company_id=company.id, document=document,
                                           client=client, on_result=tally)
        except UsageLimitExceeded as exc:
            print(f"error: stopped by a usage limit — {exc}", file=sys.stderr)
            return 1
        d = stats.as_dict()
        how = ("keyword-matched" if client else
               f"{d['requests']} chunk(s) extracted, {d['responses_rejected']} rejected,")
        print(f"    {how} {stats.entities} entities, "
              f"{stats.mentions_new} new mentions, "
              f"{stats.dropped_uncited} dropped by the citation guard")
    if args.extract == "gateway":
        print(f"  extraction cost ${spent:.4f} (ai_usage_logs, task financial_extraction)")

    # Pages, page images and a box for every paragraph, for the evidence viewer.
    try:
        from workers.render_worker import render
    except ImportError:
        print("  not rendered: the renderer needs playwright (pip install -r "
              "requirements-dev.txt) and Chrome")
    else:
        # only filings without pages: replacing the memory leaves pages as they were
        results = render(ticker="NVDA", url=dsn, only_missing=True)
        for r in results:
            print(f"  rendered {r.accession}: " + (f"error: {r.error}" if r.error else
                  f"{r.pages_with_images} pages with text, {r.boxed}/{r.paragraphs} "
                  "paragraphs boxed"))
        if not results:
            print("  pages: already rendered")

    with session_scope(dsn) as db:
        rows = db.execute(
            select(Entity.slug, Entity.name, func.count(EntityMention.id),
                   func.min(EntityMention.chunk_id))
            .join(EntityMention, EntityMention.entity_id == Entity.id)
            .group_by(Entity.slug, Entity.name)
            .order_by(func.count(EntityMention.id).desc())).all()
    web = os.environ.get("WEB_URL", "http://localhost:3000")
    print("\n  open:")
    for slug, name, n, chunk_id in rows:
        print(f"    {web}/evidence/{chunk_id}?entity={slug:<20} {name} — {n} citation(s)")
    if filings > 1:
        print(f"    {web}/timeline/nvda")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
