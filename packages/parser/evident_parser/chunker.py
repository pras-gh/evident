"""Group paragraphs into retrievable chunks without losing provenance.

Two rules the chunker will not break:

  1. A chunk never spans a section boundary. A chunk that straddles "Item 7"
     and "Item 8" cannot be cited honestly, and a retrieval hit on it would
     attribute half its text to the wrong part of the filing.
  2. A table is its own chunk. Merging a table into surrounding prose destroys
     the row/column relationship that makes the numbers mean anything.

Every chunk carries the ids of the paragraphs it was built from, so an answer
can cite "paragraph 3" rather than gesturing at a page.
"""
from __future__ import annotations

from .models import Block, Chunk, Section, Table, content_id

DEFAULT_TARGET_TOKENS = 350
DEFAULT_OVERLAP_PARAGRAPHS = 1


def _est(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_document(
    *,
    accession: str,
    blocks: list[Block],
    tables: list[Table] | None = None,
    sections: list[Section] | None = None,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_paragraphs: int = DEFAULT_OVERLAP_PARAGRAPHS,
) -> list[Chunk]:
    tables = tables or []
    chunks: list[Chunk] = []
    by_section: dict[int | None, list[Block]] = {}
    for b in blocks:
        by_section.setdefault(b.section_ordinal, []).append(b)

    for section_ordinal, items in by_section.items():
        chunks.extend(
            _chunk_section(
                accession=accession,
                section_ordinal=section_ordinal,
                blocks=items,
                target_tokens=target_tokens,
                overlap_paragraphs=overlap_paragraphs,
                start_ordinal=len(chunks),
            )
        )

    for table in tables:
        text = table.to_text()
        chunks.append(
            Chunk(
                chunk_id=content_id("c", accession, text, salt=f"table:{table.table_id}"),
                ordinal=len(chunks),
                kind="table",
                text=text,
                paragraph_ids=[],
                table_id=table.table_id,
                page_start=table.page_number,
                page_end=table.page_number,
                section_ordinal=table.section_ordinal,
            )
        )

    for i, c in enumerate(chunks):
        c.ordinal = i
    return chunks


def _chunk_section(
    *,
    accession: str,
    section_ordinal: int | None,
    blocks: list[Block],
    target_tokens: int,
    overlap_paragraphs: int,
    start_ordinal: int,
) -> list[Chunk]:
    out: list[Chunk] = []
    window: list[Block] = []
    budget = 0

    def flush() -> None:
        nonlocal window, budget
        if not window:
            return
        text = "\n\n".join(b.text for b in window)
        pages = [b.page_number for b in window if b.page_number is not None]
        out.append(
            Chunk(
                chunk_id=content_id("c", accession, text,
                                    salt=f"s{section_ordinal}:{start_ordinal + len(out)}"),
                ordinal=start_ordinal + len(out),
                kind="prose",
                text=text,
                paragraph_ids=[b.paragraph_id for b in window],
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                section_ordinal=section_ordinal,
            )
        )
        # Carry the tail forward so a sentence split across a chunk boundary is
        # still retrievable from both sides.
        window = window[-overlap_paragraphs:] if overlap_paragraphs else []
        budget = sum(_est(b.text) for b in window)

    for b in blocks:
        cost = _est(b.text)
        # A single oversized paragraph becomes its own chunk rather than being
        # split — splitting mid-paragraph would break the paragraph_id link.
        if cost >= target_tokens and window:
            flush()
        window.append(b)
        budget += cost
        if budget >= target_tokens:
            flush()
    # Don't emit a trailing chunk that is nothing but carried-over overlap.
    if window and not (out and all(b.paragraph_id in out[-1].paragraph_ids for b in window)):
        flush()
    return out
