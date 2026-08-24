"""The entities the pipeline produces.

Paragraph and table identifiers are **content-addressed**: derived from the
accession plus a normalised form of the text itself, never from a counter. Two
consequences that matter more than they look:

  * Re-ingesting an unchanged filing yields identical ids, so a citation issued
    months ago still resolves to the same paragraph.
  * Re-ingesting a *corrected* filing yields different ids for the paragraphs
    that actually changed, and identical ids for the ones that did not — so a
    diff is free, and stale citations fail loudly instead of silently pointing
    at different words.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

SourceFormat = Literal["html", "pdf"]
ChunkKind = Literal["prose", "table"]

_WS = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Collapse whitespace and case so ids survive cosmetic re-flowing.

    EDGAR re-renders filings; a paragraph that gains a line break is the same
    paragraph and must keep its id.
    """
    return _WS.sub(" ", text).strip().casefold()


def content_id(prefix: str, accession: str, text: str, *, salt: str = "") -> str:
    """A short, stable, content-addressed id: `p_1a2b3c4d5e6f`."""
    digest = hashlib.sha256(
        f"{accession}\x00{salt}\x00{normalise(text)}".encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:12]}"


@dataclass(slots=True)
class Company:
    cik: str
    name: str
    ticker: str | None = None
    sic: str | None = None


@dataclass(slots=True)
class Document:
    accession: str
    cik: str
    form_type: str
    filed_date: date
    published_at: datetime          # EDGAR acceptance datetime, not fetch time
    source_url: str
    source_format: SourceFormat
    content_sha256: str
    fiscal_period: str | None = None
    page_count: int | None = None


@dataclass(slots=True)
class Section:
    ordinal: int
    title: str
    level: int
    path: list[str]
    start_page: int | None = None
    end_page: int | None = None


@dataclass(slots=True)
class Block:
    """One paragraph of prose."""
    paragraph_id: str
    ordinal: int
    text: str
    page_number: int | None = None
    section_ordinal: int | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(slots=True)
class Table:
    """A table, kept whole. Cells are never flattened into prose."""
    table_id: str
    ordinal: int
    cells: list[list[str]]
    page_number: int | None = None
    caption: str | None = None
    section_ordinal: int | None = None

    @property
    def n_rows(self) -> int:
        return len(self.cells)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.cells), default=0)

    def to_text(self) -> str:
        """A linearisation for embedding only — the cells stay authoritative."""
        head = f"{self.caption}\n" if self.caption else ""
        return head + "\n".join(" | ".join(c for c in row) for row in self.cells)


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    ordinal: int
    kind: ChunkKind
    text: str
    paragraph_ids: list[str] = field(default_factory=list)
    table_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    section_ordinal: int | None = None

    @property
    def token_estimate(self) -> int:
        """Deliberately crude — used for chunk sizing, never for billing."""
        return max(1, len(self.text) // 4)


@dataclass(slots=True)
class ParsedDocument:
    """Everything one filing yields, before it reaches the database."""
    document: Document
    sections: list[Section] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        pages = [b.page_number for b in self.blocks if b.page_number]
        return {
            "sections": len(self.sections),
            "paragraphs": len(self.blocks),
            "tables": len(self.tables),
            "pages": max(pages) if pages else None,
            "chars": sum(b.char_count for b in self.blocks),
        }


def fiscal_period(form_type: str, report_date: str | None) -> str | None:
    """Best-effort period label from a form type and report date.

    Pure and dependency-free on purpose: it lives here rather than in the
    ingest worker so the parser package can be tested without a database
    driver installed.
    """
    if not report_date or len(report_date) < 7:
        return None
    year, month = report_date[:4], report_date[5:7]
    if not (year.isdigit() and month.isdigit()):
        return None
    if form_type.startswith("10-K"):
        return f"FY{year}"
    if form_type.startswith("10-Q"):
        return f"Q{(int(month) - 1) // 3 + 1} {year}"
    return year
