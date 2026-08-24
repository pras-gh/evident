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

import re

from .models import Block, Chunk, Section, Table, content_id

DEFAULT_TARGET_TOKENS = 350
DEFAULT_OVERLAP_PARAGRAPHS = 1
# Hard ceiling. A real 10-K contains risk-factor paragraphs of 3,000+ characters;
# left whole they become chunks of ~1,000 tokens that match everything and cite
# nothing precisely. Splitting is safe because the parts keep a derived id that
# still resolves to the source paragraph.
DEFAULT_MAX_TOKENS = 600

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\u201c\"])")


def _est(text: str) -> int:
    return max(1, len(text) // 4)


def split_oversized(block: Block, *, max_tokens: int) -> list[Block]:
    """Break a paragraph that exceeds the ceiling on sentence boundaries.

    Parts keep a derived id (`p_abc#1`, `p_abc#2`) so a citation still resolves
    to the paragraph it came from. An earlier version refused to split for fear
    of breaking that link — deriving the id keeps the link and removes the
    reason not to.
    """
    if _est(block.text) <= max_tokens:
        return [block]

    limit = max_tokens * 4                      # the estimator is chars // 4
    parts: list[str] = []
    current: list[str] = []
    length = 0

    for sentence in _SENTENCE.split(block.text):
        # A sentence longer than the whole budget has no boundary to split on —
        # usually a table of contents run together, or a list without
        # punctuation. Hard-split it rather than let the ceiling leak.
        pieces = ([sentence] if len(sentence) <= limit
                  else [sentence[i:i + limit] for i in range(0, len(sentence), limit)])
        for piece in pieces:
            extra = len(piece) + (1 if current else 0)
            if current and length + extra > limit:
                parts.append(" ".join(current))
                current, length = [], 0
                extra = len(piece)
            current.append(piece)
            length += extra
    if current:
        parts.append(" ".join(current))

    return [
        Block(paragraph_id=f"{block.paragraph_id}#{i + 1}", ordinal=block.ordinal,
              text=text, page_number=block.page_number,
              section_ordinal=block.section_ordinal)
        for i, text in enumerate(parts)
    ]


def chunk_document(
    *,
    accession: str,
    blocks: list[Block],
    tables: list[Table] | None = None,
    sections: list[Section] | None = None,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_paragraphs: int = DEFAULT_OVERLAP_PARAGRAPHS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[Chunk]:
    tables = tables or []
    blocks = [part for b in blocks for part in split_oversized(b, max_tokens=max_tokens)]
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
                max_tokens=max_tokens,
            )
        )

    for table in tables:
        # Tables were bypassing the ceiling entirely. A long financial table
        # split by rows keeps its header on every part, so each one is still
        # readable on its own — flattening or truncating it would not be.
        for i, text in enumerate(_split_table(table, max_tokens=max_tokens)):
            chunks.append(
                Chunk(
                    chunk_id=content_id("c", accession, text,
                                        salt=f"table:{table.table_id}:{i}"),
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


def _split_table(table: Table, *, max_tokens: int) -> list[str]:
    """Render a table as one or more chunk texts, each under the ceiling.

    The header row is repeated on every part. A body row without its header is
    a list of numbers with no meaning attached, which is exactly the failure
    keeping tables separate from prose was meant to avoid.
    """
    limit = max_tokens * 4
    whole = table.to_text()
    if len(whole) <= limit:
        return [whole]

    caption = f"{table.caption}\n" if table.caption else ""
    rows = [" | ".join(r) for r in table.cells]
    if not rows:
        return [whole]
    header, body = rows[0], rows[1:]

    parts: list[str] = []
    current: list[str] = []
    base = len(caption) + len(header)
    length = base
    for row in body:
        if current and length + 1 + len(row) > limit:
            parts.append(caption + "\n".join([header, *current]))
            current, length = [], base
        current.append(row)
        length += 1 + len(row)
    if current:
        parts.append(caption + "\n".join([header, *current]))
    return parts or [whole]


def _chunk_section(
    *,
    accession: str,
    section_ordinal: int | None,
    blocks: list[Block],
    target_tokens: int,
    overlap_paragraphs: int,
    start_ordinal: int,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[Chunk]:
    """Group paragraphs into chunks.

    Budgets are tracked in **characters including the separators**, because the
    token estimate is computed on the joined text. Summing per-paragraph
    estimates and comparing that to the ceiling silently under-counts by two
    characters per join, which is how chunks kept landing just over the limit.
    """
    SEP = "\n\n"
    target_chars, max_chars = target_tokens * 4, max_tokens * 4

    out: list[Chunk] = []
    window: list[Block] = []
    chars = 0

    def joined_len(items: list[Block]) -> int:
        if not items:
            return 0
        return sum(len(b.text) for b in items) + len(SEP) * (len(items) - 1)

    def flush(hard: bool = False) -> None:
        """`hard` means we flushed because the ceiling was about to be breached.
        Carrying overlap across that boundary just re-inflates the next chunk,
        so the carry is dropped."""
        nonlocal window, chars
        if not window:
            return
        text = SEP.join(b.text for b in window)
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
        # Carry the tail so a sentence split across a boundary stays retrievable
        # from both sides — but only when it is small. Carrying a large paragraph
        # re-inflates the next chunk past the ceiling.
        carry = [] if hard else (window[-overlap_paragraphs:]
                                 if overlap_paragraphs else [])
        if joined_len(carry) > target_chars // 2:
            carry = []
        window = carry
        chars = joined_len(window)

    for b in blocks:
        addition = len(b.text) + (len(SEP) if window else 0)
        if window and chars + addition > max_chars:
            flush(hard=True)
            addition = len(b.text)          # flush(hard) leaves the window empty
        window.append(b)
        chars += addition
        assert chars <= max_chars or len(window) == 1, (
            "a multi-paragraph chunk exceeded the ceiling")
        if chars >= target_chars:
            flush()

    # Do not emit a trailing chunk that is only carried-over overlap.
    if window and not (out and all(b.paragraph_id in out[-1].paragraph_ids
                                   for b in window)):
        flush()
    return out
