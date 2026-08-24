"""Entity extraction from parsed filings.

This is the one place in the pipeline where a language model belongs. Turning
"During fiscal 2025 the Company increased payments for acquisition of property,
plant and equipment" into a `metric` observation and a `topic` mention is a
reading task, not a parsing task.

The guardrail is the important part. The model is given paragraphs that already
have ids, and is required to cite one for every entity it returns. Anything
citing an id we did not supply is **dropped, not stored** — see
`drop_uncited()`. Without that, "every answer is backed by evidence" is a
slogan; with it, it is enforced at the only point where it can be.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from evident_parser.models import Block
from evident_memory.entities import (Evidence, Person, Product, Promise, Risk, Topic,
                       normalise_metric, normalise_person, slugify)

from .prompts import EXTRACT_ENTITIES, MODEL  # noqa: E402



# --------------------------------------------------------------------- schema
def _schema() -> Any:
    """Pydantic models for structured output, imported lazily.

    Kept out of module import so the rest of memory/ stays dependency-free and
    the resolution logic can be tested without pydantic or an API key.
    """
    from pydantic import BaseModel, Field

    class Cited(BaseModel):
        paragraph_id: str = Field(description="an id from the input, never invented")
        quote: str = Field(description="verbatim span supporting this entity")
        confidence: float = Field(
            ge=0.0, le=1.0,
            description=("how strongly the quoted span supports this entity: "
                         "1.0 when it states it outright, lower when it is "
                         "implied. Do not inflate — a low score is more useful "
                         "than a confident wrong one."))

    class TopicOut(Cited):
        label: str

    class PersonOut(Cited):
        full_name: str
        role: str | None = None

    class MetricOut(Cited):
        name: str
        period: str | None = None
        value: float | None = None
        unit: str | None = None

    class RiskOut(Cited):
        label: str
        category: str | None = None
        severity: str | None = None

    class PromiseOut(Cited):
        statement: str
        horizon: str | None = None

    class ProductOut(Cited):
        name: str

    class EventOut(Cited):
        kind: str
        headline: str

    class Extraction(BaseModel):
        topics: list[TopicOut] = []
        people: list[PersonOut] = []
        metrics: list[MetricOut] = []
        risks: list[RiskOut] = []
        promises: list[PromiseOut] = []
        products: list[ProductOut] = []
        events: list[EventOut] = []

    return Extraction


# ------------------------------------------------------------------ guardrail
@dataclass(slots=True)
class DropReport:
    kept: int = 0
    dropped: int = 0
    bad_ids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.bad_ids is None:
            self.bad_ids = []


def drop_uncited(items: Sequence[Any], valid_ids: set[str],
                 report: DropReport) -> list[Any]:
    """Discard anything citing a paragraph we did not supply.

    A hallucinated citation is worse than a missing entity: it looks exactly
    like a real one until someone clicks through. So it never reaches storage.
    """
    kept = []
    for item in items:
        pid = getattr(item, "paragraph_id", None)
        if pid in valid_ids:
            kept.append(item)
            report.kept += 1
        else:
            report.dropped += 1
            report.bad_ids.append(str(pid))
    return kept


def _render(blocks: Sequence[Block]) -> str:
    return "\n\n".join(f"[{b.paragraph_id}] {b.text}" for b in blocks)


# ------------------------------------------------------------------ extractor
def extract_from_blocks(
    blocks: Sequence[Block],
    *,
    document_id: str,
    observed_at: date,
    client: Any | None = None,
    max_tokens: int = 8000,
) -> tuple[dict[str, list[Any]], DropReport]:
    """Extract typed entities from one section's paragraphs.

    Returns (entities_by_kind, drop_report). The report is not decoration —
    a rising drop rate is the signal that a prompt or model change has started
    inventing citations.
    """
    if not blocks:
        return {}, DropReport()

    if client is None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "Entity extraction needs the anthropic SDK — "
                "`pip install -r requirements.txt`"
            ) from exc
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise RuntimeError(
                "No Anthropic credentials found. Run `ant auth login`, or set "
                "ANTHROPIC_API_KEY."
            )
        client = anthropic.Anthropic()

    Extraction = _schema()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        system=EXTRACT_ENTITIES.system,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": _render(blocks)}],
        output_format=Extraction,
    )
    parsed = response.parsed_output
    valid = {b.paragraph_id for b in blocks}
    pages = {b.paragraph_id: b.page_number for b in blocks}
    report = DropReport()

    out: dict[str, list[Any]] = {}
    for kind in ("topics", "people", "metrics", "risks",
                 "promises", "products", "events"):
        raw = getattr(parsed, kind, []) or []
        out[kind] = drop_uncited(raw, valid, report)

    out["_hashes"] = {b.paragraph_id: getattr(b, "chunk_hash", None) for b in blocks}
    return _to_entities(out, document_id=document_id, observed_at=observed_at,
                        pages=pages), report


def _ev(item: Any, *, document_id: str, observed_at: date,
        pages: dict[str, int | None],
        hashes: dict[str, str] | None = None) -> Evidence:
    hashes = hashes or {}
    return Evidence(
        document_id=document_id,
        paragraph_id=item.paragraph_id,
        page_number=pages.get(item.paragraph_id),
        quote=item.quote,
        observed_at=observed_at,
        chunk_hash=hashes.get(item.paragraph_id),
        confidence=getattr(item, "confidence", None),
    )


def _to_entities(raw: dict[str, list[Any]], *, document_id: str,
                 observed_at: date, pages: dict[str, int | None]) -> dict[str, list[Any]]:
    hashes = raw.get("_hashes", {})

    def ev(i):
        return _ev(i, document_id=document_id, observed_at=observed_at,
                   pages=pages, hashes=hashes)
    return {
        "topics": [Topic(slug=slugify(t.label), label=t.label,
                         first_seen_at=observed_at, last_seen_at=observed_at,
                         evidence=[ev(t)]) for t in raw.get("topics", [])],
        "people": [Person(full_name=p.full_name, normalised=normalise_person(p.full_name),
                          first_seen_at=observed_at, last_seen_at=observed_at,
                          evidence=[ev(p)]) for p in raw.get("people", [])],
        "risks": [Risk(slug=slugify(r.label), label=r.label, category=r.category,
                       first_seen_at=observed_at, last_seen_at=observed_at,
                       evidence=[ev(r)]) for r in raw.get("risks", [])],
        "promises": [Promise(statement=p.statement, made_at=observed_at,
                             made_evidence=ev(p), horizon=p.horizon)
                     for p in raw.get("promises", [])],
        "products": [Product(name=p.name, normalised=slugify(p.name),
                             first_seen_at=observed_at, last_seen_at=observed_at,
                             evidence=[ev(p)]) for p in raw.get("products", [])],
        "metrics_raw": raw.get("metrics", []),   # resolved into series in resolve.py
        "events_raw": raw.get("events", []),
        "_evidence_for": {"observed_at": observed_at, "document_id": document_id,
                          "pages": pages},
    }
