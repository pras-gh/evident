"""PDF filing parser.

The PDF path exists for filings that only ship as PDF (investor decks, some
exhibits). Page numbers here are real rather than inferred, which makes this
the easier of the two parsers — the hard part is that PDFs have no notion of a
paragraph, so blank-line runs are the only available signal.

Requires `pypdf`. Imported lazily so the rest of the package stays
dependency-free and testable without it.
"""
from __future__ import annotations

import re

from .models import Block, Section, Table, content_id
from .html import _ITEM, _PART, _MAX_HEADING_CHARS

_PARA_SPLIT = re.compile(r"\n\s*\n")


def parse_pdf(path: str, *, accession: str) -> tuple[list[Section], list[Block], list[Table], int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "PDF parsing needs pypdf — `pip install -r requirements.txt`"
        ) from exc

    reader = PdfReader(path)
    sections: list[Section] = []
    blocks: list[Block] = []
    path_stack: list[str] = []
    section_ordinal: int | None = None

    for page_index, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        for para in _PARA_SPLIT.split(raw):
            text = re.sub(r"\s+", " ", para).strip()
            if not text:
                continue

            heading_level = _heading_level(text)
            if heading_level:
                if sections:
                    sections[-1].end_page = page_index
                path_stack = ([text] if heading_level == 1
                              else path_stack[:1] + [text])
                section_ordinal = len(sections)
                sections.append(
                    Section(ordinal=section_ordinal, title=text, level=heading_level,
                            path=list(path_stack), start_page=page_index)
                )
                continue

            blocks.append(
                Block(
                    paragraph_id=content_id("p", accession, text, salt=str(len(blocks))),
                    ordinal=len(blocks),
                    text=text,
                    page_number=page_index,
                    section_ordinal=section_ordinal,
                )
            )

    if sections:
        sections[-1].end_page = len(reader.pages)
    # Table extraction from PDF needs layout analysis that pypdf does not do.
    # Returning [] is honest; see the README rather than shipping bad cells.
    return sections, blocks, [], len(reader.pages)


def _heading_level(text: str) -> int | None:
    if len(text) > _MAX_HEADING_CHARS:
        return None
    if _PART.match(text):
        return 1
    if _ITEM.match(text):
        return 2
    return None
