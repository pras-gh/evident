"""PDF filing parser.

The PDF path exists for filings that only ship as PDF (investor decks, some
exhibits, older filings). Page numbers here are real rather than inferred.

**Paragraphs come from geometry.** A PDF has no paragraphs, only glyphs at
positions, and pypdf's plain text marks a new line with one newline and a new
paragraph with... one newline. The previous parser split on blank lines that
never appear, so every page came out as a single paragraph and every citation
highlighted a whole page. Here each text run is placed on the page, runs are
gathered into lines, and a new paragraph starts where the layout says so:

    a vertical gap wider than the page's usual line spacing
    a change of font size (a heading)
    a line indented further than the one above (a first-line indent)
    a bullet
    text that moves up the page (a new column)

**Each paragraph gets its bounding box** from the same pass: the union of its
lines, a line being its first glyph's origin to its last glyph's advance, and
the font's ascent above the baseline to its descent below. Widths come from
the font's own metrics, looked up by the character's code in the font's
encoding (see `_Fonts._code_of`). That is exact for simple fonts (Type1,
TrueType — what filings use), verified against known geometry in
tests/test_pdf_parser.py. For CID fonts the way back from a character to its
glyph id is only as good as the font's ToUnicode map, and that path is not
covered by a test, so treat their right edges as approximate. Heights are
exact either way. Rotated pages and rotated text get no box rather than a
wrong one.

Paragraph ids are positional, `{page}_{index}`, like the HTML parser's, so
the evidence resolver reads a paragraph's page from its id.

Requires `pypdf`. Imported lazily so the rest of the package stays
dependency-free and testable without it.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from .html import _ITEM, _MAX_HEADING_CHARS, _PART
from .models import BBox, Block, Section, Table, positional_paragraph_id

#: A vertical step this many times the page's usual line spacing starts a new
#: paragraph. Paragraph spacing is conventionally a blank line (2×) or half of
#: one (1.5×); line spacing wobbles by a few percent with superscripts.
GAP_FACTOR = 1.35
#: A line whose left edge is this many ems right of the line above starts a
#: new paragraph — a first-line indent.
INDENT_EMS = 0.8
#: A font-size change of more than this many points starts a new paragraph.
SIZE_STEP = 1.5
_BULLET = re.compile(r"^\s*[•●◦▪‣∙·\-–]\s")


@dataclass(slots=True)
class _Line:
    text: str = ""
    baseline: float | None = None      # y of the baseline, from the page's top
    x0: float | None = None
    x1: float | None = None
    size: float = 0.0
    ascent: float = 0.0
    descent: float = 0.0
    measurable: bool = True            # False if any glyph was rotated

    @property
    def top(self) -> float:
        return (self.baseline or 0.0) - self.ascent

    @property
    def bottom(self) -> float:
        return (self.baseline or 0.0) + self.descent


@dataclass(slots=True)
class _Page:
    number: int
    width: float
    height: float
    lines: list[_Line] = field(default_factory=list)
    has_geometry: bool = True


def _mul(m: list[float], n: list[float]) -> list[float]:
    """Compose two PDF matrices [a b c d e f]: m, then n."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F]


class _Fonts:
    """Glyph widths and vertical metrics, per font dictionary."""

    def __init__(self) -> None:
        self._cache: dict[int, Any] = {}
        self._codes: dict[int, dict[str, str]] = {}

    def get(self, font_dict: Any) -> Any:
        if font_dict is None:
            return None
        key = id(font_dict)
        if key not in self._cache:
            try:
                # pypdf's own font model: parses /Widths, /W and the metrics
                # of the 14 standard fonts. Private, so guarded — without it
                # widths fall back to half an em, heights to typical values.
                from pypdf._font import Font
                self._cache[key] = Font.from_font_resource(font_dict)
            except Exception:
                self._cache[key] = None
        return self._cache[key]

    def _code_of(self, font: Any) -> dict[str, str]:
        """Decoded character → the code its width is filed under.

        A font's widths are indexed by character *code* — `’` is byte 0x92 in
        WinAnsi — while extraction hands over decoded text. Looking `’` up
        directly finds nothing and silently falls back to a default width, so
        every line with a curly quote, dash or bullet would be mis-measured.
        The font's own encoding, and its ToUnicode map where it has one, say
        which code each character came from.
        """
        key = id(font)
        if key not in self._codes:
            inverse: dict[str, str] = {}
            if isinstance(font.encoding, dict):
                for code, ch in font.encoding.items():
                    inverse.setdefault(ch, chr(code))
            for code, ch in (font.character_map or {}).items():
                if isinstance(code, str) and isinstance(ch, str) and len(ch) == 1:
                    # a multi-byte code is a CID; widths are filed under chr(cid)
                    inverse[ch] = (code if len(code) == 1 else
                                   chr(int.from_bytes(code.encode("latin-1", "replace"), "big")))
            self._codes[key] = inverse
        return self._codes[key]

    def advance(self, font: Any, text: str) -> float:
        """Width of `text` in thousandths of an em."""
        if font is None:
            return 500.0 * len(text)
        widths = font.character_widths
        default = widths.get("default", 500)
        code_of = self._code_of(font)
        total = 0.0
        for ch in text:
            code = code_of.get(ch)
            if code is None and isinstance(font.encoding, str):
                try:
                    encoded = ch.encode(font.encoding)
                    code = chr(encoded[0]) if len(encoded) == 1 else None
                except (LookupError, UnicodeError):
                    code = None
            total += widths.get(code, widths.get(ch, default)) if code else widths.get(ch, default)
        return total

    @staticmethod
    def vertical(font: Any) -> tuple[float, float]:
        """(ascent, descent) in thousandths of an em, both positive."""
        if font is None:
            return 750.0, 250.0
        fd = font.font_descriptor
        return float(fd.ascent or 750.0), abs(float(fd.descent or -250.0))


def _read_page(page: Any, number: int, fonts: _Fonts) -> _Page:
    box = page.cropbox
    left, top = float(box.left), float(box.top)
    out = _Page(number=number, width=float(box.width), height=float(box.height),
                has_geometry=int(page.rotation or 0) % 360 == 0)
    line = _Line()

    def end_line() -> None:
        nonlocal line
        if line.text.strip():
            out.lines.append(line)
        line = _Line()

    def add(text: str, cm: list[float], tm: list[float], font_dict: Any,
            font_size: float) -> None:
        if not text:
            return
        m = _mul(tm, cm)
        rotated = abs(m[1]) > 1e-3 or abs(m[2]) > 1e-3
        scale_x, scale_y = abs(m[0]) or 1.0, abs(m[3]) or 1.0
        size = font_size * scale_y
        font = fonts.get(font_dict)
        ascent, descent = fonts.vertical(font)
        x = m[4] - left
        baseline = top - m[5]

        pieces = text.split("\n")
        for i, piece in enumerate(pieces):
            if i:                       # every newline ends the line before it
                end_line()
            if not piece:
                continue
            if line.text and not line.text.endswith(" ") and not piece.startswith(" ") \
                    and line.x1 is not None and x - line.x1 > 0.15 * size:
                line.text += " "        # runs placed apart are separate words
            line.text += piece
            if rotated:
                line.measurable = False
                continue
            visible = piece.strip()
            if not visible:
                continue
            lead = len(piece) - len(piece.lstrip())
            start = x + fonts.advance(font, piece[:lead]) / 1000 * font_size * scale_x
            end = start + fonts.advance(font, visible) / 1000 * font_size * scale_x
            if line.baseline is None:
                line.baseline = baseline
            line.x0 = start if line.x0 is None else min(line.x0, start)
            line.x1 = end if line.x1 is None else max(line.x1, end)
            line.size = max(line.size, size)
            line.ascent = max(line.ascent, ascent / 1000 * size)
            line.descent = max(line.descent, descent / 1000 * size)

    page.extract_text(visitor_text=add)
    end_line()
    return out


def _paragraphs(page: _Page) -> list[list[_Line]]:
    """Group a page's lines into paragraphs by the rules in the module doc."""
    placed = [ln for ln in page.lines if ln.baseline is not None]
    steps = [b.baseline - a.baseline for a, b in zip(placed, placed[1:])
             if a.baseline is not None and b.baseline is not None
             and 0 < b.baseline - a.baseline < 3 * max(a.size, b.size, 1.0)]
    sizes = [ln.size for ln in placed if ln.size]
    leading = (statistics.median(steps) if steps
               else 1.2 * (statistics.median(sizes) if sizes else 10.0))

    groups: list[list[_Line]] = []
    for ln in page.lines:
        prev = groups[-1][-1] if groups else None
        if prev is None or _breaks(prev, ln, leading):
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return groups


def _breaks(prev: _Line, ln: _Line, leading: float) -> bool:
    if _BULLET.match(ln.text):
        return True
    if prev.baseline is None or ln.baseline is None:
        return False
    step = ln.baseline - prev.baseline
    if step <= 0:                                   # moved up: a new column
        return True
    if step > GAP_FACTOR * leading:
        return True
    if prev.size and ln.size and abs(prev.size - ln.size) > SIZE_STEP:
        return True
    if prev.x0 is not None and ln.x0 is not None and ln.size \
            and ln.x0 - prev.x0 > INDENT_EMS * ln.size:
        return True
    return False


def _box(page: _Page, lines: list[_Line]) -> BBox | None:
    if not page.has_geometry or not all(ln.measurable for ln in lines):
        return None
    placed = [ln for ln in lines if ln.x0 is not None and ln.baseline is not None]
    if not placed:
        return None
    return BBox(page=page.number,
                x0=max(0.0, min(ln.x0 for ln in placed)),
                y0=max(0.0, min(ln.top for ln in placed)),
                x1=min(page.width, max(ln.x1 for ln in placed)),
                y1=min(page.height, max(ln.bottom for ln in placed)),
                page_width=page.width, page_height=page.height)


def parse_pdf(path: str, *, accession: str) -> tuple[list[Section], list[Block], list[Table], int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "PDF parsing needs pypdf — `pip install -r requirements-dev.txt`"
        ) from exc

    reader = PdfReader(path)
    fonts = _Fonts()
    sections: list[Section] = []
    blocks: list[Block] = []
    path_stack: list[str] = []
    section_ordinal: int | None = None

    for page_index, pdf_page in enumerate(reader.pages, start=1):
        page = _read_page(pdf_page, page_index, fonts)
        index_on_page = 0
        for lines in _paragraphs(page):
            text = re.sub(r"\s+", " ", " ".join(ln.text for ln in lines)).strip()
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

            index_on_page += 1
            blocks.append(
                Block(
                    paragraph_id=positional_paragraph_id(page_index, index_on_page),
                    ordinal=len(blocks),
                    text=text,
                    page_number=page_index,
                    section_ordinal=section_ordinal,
                    bbox=_box(page, lines),
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
