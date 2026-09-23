#!/usr/bin/env python
"""Seed a local database so the evidence viewer has something real to show.

    DATABASE_URL=... python tools/seed_demo.py                     # one filing
    DATABASE_URL=... python tools/seed_demo.py --corpus timeline   # three years
    open http://localhost:3000/evidence/nvda/export_controls
    open http://localhost:3000/timeline/nvda

**This is not Claude.** Entities are found by a keyword matcher, so the demo
runs without an API key. What it produces is still truthful — every citation
points at a paragraph that really contains the named thing, and every quote is
a real sentence from that paragraph — but it finds only the handful of names in
`VOCABULARY`, and it has no judgment about what a filing is saying.

It drives the same `build_for_document` the worker runs, through the same
Pydantic gate and citation guard, with the keyword matcher standing in only for
the network call. So the viewer is exercised against the real storage path,
not a hand-built fixture.

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", choices=sorted(CORPORA), default="bench")
    args = ap.parse_args(argv)
    corpus, filings = CORPORA[args.corpus]

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: set DATABASE_URL (a migrated database)", file=sys.stderr)
        return 2

    from sqlalchemy import func, select

    from evident_db import Company, Document, Entity, EntityMention, session_scope
    from test_ingest_e2e import fixture_origin
    from workers.ingest_worker import ingest_ticker
    from workers.memory_builder import build_for_document

    with fixture_origin(corpus):
        ingested = ingest_ticker("NVDA", form_types=["10-K"], limit=filings,
                                 url=dsn).filings
    # oldest first, so first_seen and latest_seen advance the way they would
    # if each filing had been processed the day it was published
    for filing in sorted(ingested, key=lambda f: f.filed_at):
        print(f"  {filing.form_type} {filing.accession}: "
              + ("already ingested, unchanged" if filing.skipped else f"{filing.chunks} chunks"))
        with session_scope(dsn) as db:
            company = db.execute(select(Company).where(Company.ticker == "NVDA")).scalar_one()
            document = db.execute(select(Document).where(
                Document.accession == filing.accession)).scalar_one()
            stats = build_for_document(db, company_id=company.id, document=document,
                                       client=KeywordClient())
            print(f"    keyword-matched {stats.entities} entities, "
                  f"{stats.mentions_new} new mentions, "
                  f"{stats.dropped_uncited} dropped by the citation guard")

    with session_scope(dsn) as db:
        rows = db.execute(
            select(Entity.slug, Entity.name, func.count(EntityMention.id))
            .join(EntityMention, EntityMention.entity_id == Entity.id)
            .group_by(Entity.slug, Entity.name)
            .order_by(func.count(EntityMention.id).desc())).all()
    web = os.environ.get("WEB_URL", "http://localhost:3000")
    print("\n  open:")
    for slug, name, n in rows:
        print(f"    {web}/evidence/nvda/{slug:<22} {name} — {n} citation(s)")
    if filings > 1:
        print(f"    {web}/timeline/nvda")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
