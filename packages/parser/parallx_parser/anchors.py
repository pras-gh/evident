"""Anchors: how a stored chunk maps back to the paragraphs a reader can see.

A chunk is several paragraphs joined with `PARAGRAPH_SEP`, stored alongside the
ids of those paragraphs in order. Splitting the text on the same separator gives
the paragraphs back, one per id — verified across every prose chunk of the
fixtures, 435 of 435.

The separator is defined here and imported by the chunker, so the thing that
joins and the thing that splits cannot drift apart. A mismatch would not error;
it would silently attach one paragraph's text to its neighbour's id, and every
highlight after that point would land one paragraph off.

Every citable unit has an anchor:

    prose paragraph   p-{paragraph_id}
    table chunk       c-{chunk_id}      (tables carry no paragraph ids)
"""
from __future__ import annotations

from dataclasses import dataclass

#: Joins a chunk's paragraphs. The chunker budgets its size including these
#: characters, so changing it changes chunk boundaries for every document.
PARAGRAPH_SEP = "\n\n"


@dataclass(frozen=True, slots=True)
class Paragraph:
    paragraph_id: str
    page: int | None
    text: str

    @property
    def anchor(self) -> str:
        return paragraph_anchor(self.paragraph_id)


def paragraph_anchor(paragraph_id: str) -> str:
    return f"p-{paragraph_id}"


def chunk_anchor(chunk_id: int) -> str:
    return f"c-{chunk_id}"


def paragraph_page(paragraph_id: str | None, fallback: int | None = None) -> int | None:
    """The page a paragraph is on, read from its id.

    Positional ids are `{page}_{index}`, with a `#n` suffix when an oversized
    paragraph was split on sentence boundaries — `27_3` and `27_3#2` are both
    page 27. That is more precise than the chunk's stored `page_number`, which
    is only the page the chunk *starts* on: chunks routinely span pages, and a
    citation resolved to the chunk's page scrolls to the wrong one for every
    paragraph after the break.

    Page 0 is what the chunker writes when the page was unknown, so it is not
    trusted over `fallback`.
    """
    if not paragraph_id:
        return fallback
    head = paragraph_id.split("#", 1)[0].split("_", 1)[0]
    try:
        page = int(head)
    except ValueError:
        return fallback
    return page if page > 0 else fallback


def split_chunk(text: str, paragraph_ids: list[str] | None,
                page_fallback: int | None = None) -> list[Paragraph] | None:
    """A chunk's paragraphs, in order, each with its id and page.

    Returns None when the chunk cannot be split faithfully — a table chunk,
    which has no paragraph ids, or text whose parts do not line up with its ids.
    The caller then treats the chunk as one unit anchored by `c-{chunk_id}`.
    Guessing an alignment would put the wrong text under a citation, which is
    the one failure this whole layer exists to prevent.
    """
    if not paragraph_ids:
        return None
    parts = text.split(PARAGRAPH_SEP)
    if len(parts) != len(paragraph_ids):
        return None
    return [Paragraph(pid, paragraph_page(pid, page_fallback), part)
            for pid, part in zip(paragraph_ids, parts)]
