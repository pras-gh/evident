"""A minimal PDF writer for tests: text at known positions, nothing else.

Real PDFs cannot be generated with known geometry without a layout library,
and a test that checks bounding boxes against the extractor's own idea of
where text is proves nothing. This writes paragraphs line by line in Courier —
every glyph 600/1000 em wide — and returns exactly where it put each one, so
the boxes the parser recovers can be checked against ground truth.

    layout = write_pdf(path, pages=[[para, para], [para]])
    layout[0].box   # (page, x0, top, x1, bottom) in points, origin top-left
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
CHAR_W = 0.6 * FONT_SIZE                # Courier: 600/1000 em
ASCENT, DESCENT = 0.629 * FONT_SIZE, 0.157 * FONT_SIZE   # Courier AFM
COLUMNS = 80


@dataclass(frozen=True)
class Placed:
    page: int
    text: str                            # the paragraph, lines joined by spaces
    lines: tuple[str, ...]
    box: tuple[float, float, float, float]   # x0, top, x1, bottom; top-left origin


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: str | Path, pages: list[list[str]], *, columns: int = COLUMNS,
              x: float = MARGIN_X, media_origin: tuple[float, float] = (0.0, 0.0)
              ) -> list[Placed]:
    """Lay each page's paragraphs out top to bottom and write the file.

    `media_origin` shifts the MediaBox, as some producers do, to check the
    parser measures from the page's corner rather than from (0, 0).
    """
    ox, oy = media_origin
    placed: list[Placed] = []
    streams: list[bytes] = []
    for page_no, paragraphs in enumerate(pages, start=1):
        ops = ["BT", f"/F1 {FONT_SIZE:g} Tf"]
        baseline = PAGE_H - MARGIN_TOP - ASCENT        # from the page's bottom
        for para in paragraphs:
            lines = tuple(textwrap.wrap(para, columns)) or ("",)
            first_baseline = baseline
            for line in lines:
                ops.append(f"1 0 0 1 {ox + x:.2f} {oy + baseline:.2f} Tm ({_escape(line)}) Tj")
                baseline -= LEADING
            last_baseline = baseline + LEADING
            width = max(len(l) for l in lines) * CHAR_W
            top = PAGE_H - (first_baseline + ASCENT)
            bottom = PAGE_H - (last_baseline - DESCENT)
            placed.append(Placed(page_no, " ".join(lines), lines,
                                 (x, top, x + width, bottom)))
            baseline -= PARA_GAP
        ops.append("ET")
        streams.append("\n".join(ops).encode("latin-1"))

    # objects: 1 catalog, 2 pages, 3 font, then (page, content) per page
    n = len(pages)
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: ("<< /Type /Pages /Count %d /Kids [%s] >>" % (
            n, " ".join(f"{4 + 2 * i} 0 R" for i in range(n)))).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
    }
    for i, stream in enumerate(streams):
        page_id, content_id = 4 + 2 * i, 5 + 2 * i
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [{ox:g} {oy:g} {ox + PAGE_W:g} {oy + PAGE_H:g}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objs[content_id] = (b"<< /Length %d >>\nstream\n" % len(stream)
                            + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for num in sorted(objs):
        out += b"%010d 00000 n \n" % offsets[num]
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    Path(path).write_bytes(bytes(out))
    return placed
