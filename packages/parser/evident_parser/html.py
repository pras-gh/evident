"""HTML filing parser — standard library only.

SEC HTML is machine-generated and structurally poor: headings are usually bold
text in a <div>, not <h1>, and pages exist only as break markers. So the parser
is deliberately heuristic, and every heuristic is named and testable rather than
buried in a regex soup.

Three things it must not lose:
  * page number — carried as a running counter over page-break markers
  * section path — built from Part/Item headings as they appear
  * tables       — captured as cell matrices, never merged into prose
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from .models import Block, ParsedDocument, Section, Table, content_id

# Text that starts a new section. Ordered — Part is coarser than Item.
_PART = re.compile(r"^part\s+([ivx]+)\b", re.I)
_ITEM = re.compile(r"^item\s+(\d+[a-z]?)\s*[.\-–—:]?\s*(.*)$", re.I)

# A page break in SEC HTML is a style, not an element.
_PAGE_BREAK = re.compile(r"page-break-(?:before|after)\s*:\s*always", re.I)

_BLOCK_TAGS = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
# ix:header carries the inline-XBRL context/unit declarations — roughly 19KB of
# machine metadata in a real 10-K, with no sentence structure. Ingesting it
# produces a single enormous junk chunk at the top of the document.
_SKIP_TAGS = {"script", "style", "head", "ix:header", "ix:references",
              "ix:resources", "ix:hidden"}
# Void elements never close, so they must not affect nesting depth.
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.I)
_MAX_HEADING_CHARS = 120


class _FilingParser(HTMLParser):
    def __init__(self, accession: str) -> None:
        super().__init__(convert_charrefs=True)
        self.accession = accession
        self.page = 1
        self.sections: list[Section] = []
        self.blocks: list[Block] = []
        self.tables: list[Table] = []

        self._buf: list[str] = []
        self._skip_depth = 0
        self._depth = 0
        self._hidden_at: int | None = None
        self._section_ordinal: int | None = None
        self._path: list[str] = []

        # table state
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] = []
        self._cell: list[str] | None = None

    # ---------------------------------------------------------------- helpers
    def _page_break(self, attrs: list[tuple[str, str | None]]) -> bool:
        for name, value in attrs:
            if value and name in ("style", "class") and _PAGE_BREAK.search(value):
                return True
        return False

    def _flush(self) -> None:
        """Emit whatever prose has accumulated as one paragraph or heading."""
        text = " ".join(self._buf).strip()
        self._buf.clear()
        text = re.sub(r"\s+", " ", text)
        if not text:
            return

        if self._maybe_section(text):
            return

        self.blocks.append(
            Block(
                paragraph_id=content_id("p", self.accession, text,
                                        salt=str(len(self.blocks))),
                ordinal=len(self.blocks),
                text=text,
                page_number=self.page,
                section_ordinal=self._section_ordinal,
            )
        )

    def _maybe_section(self, text: str) -> bool:
        """A heading is short *and* matches a Part/Item pattern.

        The length guard matters: body prose frequently opens with a
        cross-reference like "Item 7 of this report describes …", and without
        it every such sentence would start a bogus section.
        """
        if len(text) > _MAX_HEADING_CHARS:
            return False

        part = _PART.match(text)
        if part:
            self._path = [text]
            self._open_section(text, level=1)
            return True

        item = _ITEM.match(text)
        if item:
            base = self._path[:1]
            self._path = base + [text]
            self._open_section(text, level=2)
            return True
        return False

    def _open_section(self, title: str, *, level: int) -> None:
        if self.sections:
            self.sections[-1].end_page = self.page
        ordinal = len(self.sections)
        self.sections.append(
            Section(ordinal=ordinal, title=title, level=level,
                    path=list(self._path), start_page=self.page)
        )
        self._section_ordinal = ordinal

    # ------------------------------------------------------------- callbacks
    def _hidden(self, attrs: list[tuple[str, str | None]]) -> bool:
        """Filings hide XBRL plumbing behind `display:none` rather than omitting
        it. A browser never shows it, so neither should we."""
        for name, value in attrs:
            if name == "style" and value and _DISPLAY_NONE.search(value):
                return True
        return False

    def handle_startendtag(self, tag, attrs):
        # `<br/>` and friends: start and end in one token, so depth is untouched.
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID_TAGS:
            self._depth += 1
        if self._hidden_at is None and self._hidden(attrs):
            self._flush()
            self._hidden_at = self._depth
            return
        if self._hidden_at is not None:
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._page_break(attrs):
            self._flush()
            self.page += 1
        if tag == "table":
            self._flush()
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows, self._row, self._cell = [], [], None
            return
        if self._table_depth:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = []
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        depth_before = self._depth
        if tag not in _VOID_TAGS:
            self._depth = max(0, self._depth - 1)
        if self._hidden_at is not None:
            if depth_before <= self._hidden_at:
                self._hidden_at = None
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._table_depth:
            if tag in ("td", "th") and self._cell is not None:
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
                self._cell = None
            elif tag == "tr":
                if any(c for c in self._row):
                    self._rows.append(self._row)
                self._row = []
            elif tag == "table":
                self._table_depth -= 1
                if self._table_depth == 0:
                    self._close_table()
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth or self._hidden_at is not None:
            return
        if self._table_depth:
            if self._cell is not None:
                self._cell.append(data)
            return
        self._buf.append(data)

    # ---------------------------------------------------------------- tables
    def _close_table(self) -> None:
        rows = [r for r in self._rows if any(c.strip() for c in r)]
        self._rows = []
        if not rows:
            return
        # SEC wraps layout in tables constantly; a single-column "table" is
        # almost always a layout artefact, so treat it as prose instead.
        if max(len(r) for r in rows) < 2:
            text = re.sub(r"\s+", " ", " ".join(c for r in rows for c in r)).strip()
            if text:
                self._buf.append(text)
                self._flush()
            return

        flat = "\n".join("|".join(r) for r in rows)
        self.tables.append(
            Table(
                table_id=content_id("t", self.accession, flat,
                                    salt=str(len(self.tables))),
                ordinal=len(self.tables),
                cells=rows,
                page_number=self.page,
                section_ordinal=self._section_ordinal,
            )
        )

    def close(self):  # type: ignore[override]
        super().close()
        self._flush()
        if self.sections:
            self.sections[-1].end_page = self.page


def parse_html(html: str, *, accession: str) -> tuple[list[Section], list[Block], list[Table], int]:
    """Return (sections, blocks, tables, page_count)."""
    parser = _FilingParser(accession)
    parser.feed(html)
    parser.close()
    return parser.sections, parser.blocks, parser.tables, parser.page


def attach_to(doc: ParsedDocument, html: str) -> ParsedDocument:
    sections, blocks, tables, pages = parse_html(html, accession=doc.document.accession)
    doc.sections, doc.blocks, doc.tables = sections, blocks, tables
    doc.document.page_count = pages
    return doc
