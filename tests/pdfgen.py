"""A minimal PDF writer for tests: text at known positions, nothing else.

Real PDFs cannot be generated with known geometry without a layout library,
and a test that checks bounding boxes against the extractor's own idea of
where text is proves nothing. This writes paragraphs line by line and returns
exactly where it put each one, so the boxes a parser recovers can be checked
against ground truth.

    layout = write_pdf(path, pages=[[para, para], [para]])
    layout[0].box   # (x0, top, x1, bottom) in points, origin top-left

The knobs are the ways real filings differ:

    font="custom"   a non-standard font carrying its own irregular /Widths and
                    /FontDescriptor, so widths must be read from the file
    size_via="tm"   font size set in the text matrix (`1 Tf`, `10 0 0 10 … Tm`)
    size_via="cm"   …or in the page transform (`20 Tf` under `0.5 … cm`)
    para_gap=0      paragraphs separated only by a first-line `indent`
    rotate=90       a rotated page
    media_origin    a MediaBox that does not start at (0, 0)
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

PAGE_W, PAGE_H = 612.0, 792.0          # US Letter
MARGIN_X, MARGIN_TOP = 72.0, 72.0
FONT_SIZE = 10.0
LEADING = 12.0                          # baseline to baseline within a paragraph
PARA_GAP = 12.0                         # extra space between paragraphs
COLUMNS = 80

COURIER = {"ascent": 629.0, "descent": 157.0}          # Courier AFM
CUSTOM = {"ascent": 800.0, "descent": 200.0}           # declared in the file


def custom_width(ch: str) -> int:
    """Deliberately irregular, so a parser using any standard metrics is wrong."""
    return 400 + (ord(ch) % 7) * 50


@dataclass(frozen=True)
class Placed:
    page: int
    text: str                            # the paragraph, lines joined by spaces
    lines: tuple[str, ...]
    box: tuple[float, float, float, float]   # x0, top, x1, bottom; top-left origin


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _width(text: str, font: str) -> float:
    """Advance of `text` in ems. Widths are indexed by the byte each
    character is written as — `’` is 0x92 — exactly as a PDF reader looks
    them up, so ground truth for non-ASCII text is right too."""
    if font == "courier":
        return 0.6 * len(text)
    return sum(custom_width(chr(b)) for b in text.encode("cp1252")) / 1000


def paginate(paragraphs: list[str], *, columns: int = COLUMNS,
             first_page_reserved: int = 0) -> list[list[str]]:
    """Pack paragraphs into pages that fit, in order. `first_page_reserved`
    lines are left free on page one, e.g. for a heading."""
    usable = PAGE_H - 2 * MARGIN_TOP
    pages: list[list[str]] = [[]]
    used = first_page_reserved * LEADING + (PARA_GAP if first_page_reserved else 0)
    for para in paragraphs:
        height = len(textwrap.wrap(para, columns)) * LEADING
        if height > usable:
            raise ValueError("a paragraph longer than a page")
        if pages[-1] and used + height > usable:
            pages.append([])
            used = 0.0
        pages[-1].append(para)
        used += height + PARA_GAP
    return pages


def write_pdf(path: str | Path, pages: list[list[str]], *, columns: int = COLUMNS,
              x: float = MARGIN_X, media_origin: tuple[float, float] = (0.0, 0.0),
              font: str = "courier", size_via: str = "tf", para_gap: float = PARA_GAP,
              indent: float = 0.0, rotate: int = 0) -> list[Placed]:
    ox, oy = media_origin
    metrics = COURIER if font == "courier" else CUSTOM
    ascent, descent = metrics["ascent"] / 1000 * FONT_SIZE, metrics["descent"] / 1000 * FONT_SIZE

    placed: list[Placed] = []
    streams: list[bytes] = []
    for page_no, paragraphs in enumerate(pages, start=1):
        if size_via == "cm":
            ops = ["q", "0.5 0 0 0.5 0 0 cm", "BT", f"/F1 {2 * FONT_SIZE:g} Tf"]
        elif size_via == "tm":
            ops = ["BT", "/F1 1 Tf"]
        else:
            ops = ["BT", f"/F1 {FONT_SIZE:g} Tf"]

        def show(line: str, lx: float, baseline: float) -> None:
            px, py = ox + lx, oy + baseline
            if size_via == "cm":        # coordinates are halved by the cm
                ops.append(f"1 0 0 1 {2 * px:.3f} {2 * py:.3f} Tm")
            elif size_via == "tm":
                ops.append(f"{FONT_SIZE:g} 0 0 {FONT_SIZE:g} {px:.3f} {py:.3f} Tm")
            else:
                ops.append(f"1 0 0 1 {px:.3f} {py:.3f} Tm")
            ops.append(f"({_escape(line)}) Tj")

        baseline = PAGE_H - MARGIN_TOP - ascent          # from the page's bottom
        for para in paragraphs:
            lines = tuple(textwrap.wrap(para, columns)) or ("",)
            first_baseline, left, right = baseline, float("inf"), 0.0
            for i, line in enumerate(lines):
                lx = x + (indent if i == 0 else 0.0)
                show(line, lx, baseline)
                left = min(left, lx)
                right = max(right, lx + _width(line, font) * FONT_SIZE)
                baseline -= LEADING
            last_baseline = baseline + LEADING
            box = (left, PAGE_H - (first_baseline + ascent),
                   right, PAGE_H - (last_baseline - descent))
            if box[2] > PAGE_W or box[3] > PAGE_H:
                # ground truth for text off the page is meaningless; the
                # fixture is wrong, not the parser
                raise ValueError(f"page {page_no}: text runs off the page {box}; "
                                 "use fewer columns or fewer paragraphs per page")
            placed.append(Placed(page_no, " ".join(lines), lines, box))
            baseline -= para_gap
        ops.append("ET")
        if size_via == "cm":
            ops.append("Q")
        streams.append("\n".join(ops).encode("cp1252"))

    n = len(pages)
    if font == "courier":
        font_obj = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
        extra: dict[int, bytes] = {}
    else:
        widths = " ".join(str(custom_width(chr(c))) for c in range(32, 256))
        font_obj = (b"<< /Type /Font /Subtype /Type1 /BaseFont /EvidentTest "
                    b"/Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 255 "
                    b"/Widths [" + widths.encode() + b"] /FontDescriptor 4 0 R >>")
        extra = {4: (b"<< /Type /FontDescriptor /FontName /EvidentTest /Flags 32 "
                     b"/FontBBox [0 -200 1000 800] /ItalicAngle 0 /Ascent 800 "
                     b"/Descent -200 /CapHeight 700 /StemV 80 >>")}

    first_page = 5
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: ("<< /Type /Pages /Count %d /Kids [%s] >>" % (
            n, " ".join(f"{first_page + 2 * i} 0 R" for i in range(n)))).encode(),
        3: font_obj, **extra,
    }
    for i, stream in enumerate(streams):
        page_id, content_id = first_page + 2 * i, first_page + 1 + 2 * i
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [{ox:g} {oy:g} {ox + PAGE_W:g} {oy + PAGE_H:g}] "
            + (f"/Rotate {rotate} " if rotate else "")
            + f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objs[content_id] = (b"<< /Length %d >>\nstream\n" % len(stream)
                            + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    size = max(objs) + 1
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += (b"%010d 00000 n \n" % offsets[num]) if num in offsets else b"0000000000 65535 f \n"
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref))
    Path(path).write_bytes(bytes(out))
    return placed
