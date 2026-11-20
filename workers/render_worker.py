"""Render filings to pages: an image per page, a thumbnail, a PDF, and a
bounding box for every paragraph.

    python -m workers.render_worker NVDA            # every filing for a ticker
    python -m workers.render_worker --all

An HTML filing — every real 10-K — has no page geometry until a browser lays
it out, so this lays it out in headless Chrome (Playwright, driving the
installed Chrome) at US Letter width, and:

1. **finds the pages** the parser found, by the parser's own rule: a page
   starts at any visible element whose `style` or `class` says
   `page-break-before/after: always` (`evident_parser.html`);
2. **finds every stored paragraph** in the laid-out text — compared with all
   whitespace removed, so inline markup (`Export<b>controls</b>`) and entity
   spacing cannot break the match — and measures it with a DOM Range;
3. **photographs each page** from the same layout.

So a box is measured on the very pixels it is later drawn over. A paragraph
that cannot be found, or that lands on a different page from the one its id
names, gets no box and is reported: a missing highlight is recoverable, a
highlight on the wrong words is the failure this product exists to prevent.

A PDF filing already has boxes from parse time; it gets page sizes and its own
bytes as the PDF. Page images for PDFs need a rasteriser, which is not wired
up yet — the viewer shows those filings as text.
"""
from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from evident_db import Chunk, Company, Document, DocumentPage, session_scope, store
from evident_parser.anchors import split_chunk

log = logging.getLogger("evident.render_worker")

#: US Letter at 96 CSS px per inch: 8.5 in × 11 in.
PAGE_WIDTH_PX = 816
PAGE_HEIGHT_PX = 1056
#: points per CSS pixel (72 / 96)
PT_PER_PX = 0.75
#: page images at 2× so text stays sharp on high-density screens
SCALE = 2
THUMB_WIDTH = 180
RENDERER = "chrome-html-pages/1"

#: Applied after the pages are photographed, for the PDF: break exactly where
#: the filing's pages begin and nowhere else, so PDF page N is page N.
PRINT_CSS = """
* { break-before: auto !important; break-after: auto !important;
    page-break-before: auto !important; page-break-after: auto !important;
    break-inside: auto !important; }
[data-evident-break] { break-before: page !important; page-break-before: always !important; }
"""

#: For filings that bring no styling of their own (the test corpora are bare
#: <p> tags): something a reader would recognise as a filing page.
BASE_CSS = """
html { background: #fff; }
body { margin: 0; padding: 0 84px; color: #111;
       font: 13.5px/1.5 "Times New Roman", Times, serif; }
p { margin: 0 0 10px; text-align: justify; hyphens: none; }
"""

#: Runs in the page. Mirrors evident_parser.html's page-break rule exactly —
#: if the two ever count pages differently, every page number is off by the
#: difference, so the parity is tested (tests/test_render_worker.py).
MEASURE_JS = r"""
({ paragraphs, minHeight, topPad, styled }) => {
  const BREAK = /page-break-(?:before|after)\s*:\s*always/i;
  const NONE = /display\s*:\s*none/i;
  const inlineHidden = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const s = e.getAttribute("style");
      if (s && NONE.test(s)) return true;
    }
    return false;
  };
  // Where an element starts in the flow. A break element hidden by a
  // stylesheet has no box of its own, so a zero-size marker stands in for it.
  const docY = (el) => {
    if (el.getClientRects().length) return el.getBoundingClientRect().top + window.scrollY;
    const m = document.createElement("span");
    m.style.cssText = "display:block;height:0;margin:0;padding:0";
    el.before(m);
    const y = m.getBoundingClientRect().top + window.scrollY;
    m.remove();
    return y;
  };

  // Broken images (a filing's charts are not fetched) keep their space but
  // are not drawn as broken-image icons.
  for (const img of document.images) {
    if (!img.complete || img.naturalWidth === 0) img.style.visibility = "hidden";
  }

  const breaks = [...document.body.querySelectorAll("*")].filter((el) => {
    const marks = `${el.getAttribute("style") || ""} ${el.getAttribute("class") || ""}`;
    return BREAK.test(marks) && !inlineHidden(el);
  });
  // so the PDF can break exactly here and nowhere else
  for (const el of breaks) el.setAttribute("data-evident-break", "");

  const spacer = (h) => {
    const d = document.createElement("div");
    d.setAttribute("data-evident-spacer", "");
    d.style.cssText = `display:block;height:${h}px;margin:0;padding:0;border:0`;
    return d;
  };

  // Unstyled filings get page margins: space after each break (the top of the
  // next page), and every page padded to at least a Letter page's height.
  if (!styled) {
    document.body.insertBefore(spacer(topPad), document.body.firstChild);
    for (const el of breaks) el.after(spacer(topPad));
  }
  let top = 0;
  for (const el of breaks) {
    const h = docY(el) - top;
    if (h < minHeight) el.before(spacer(minHeight - h));
    top = docY(el);
  }
  const end = document.documentElement.scrollHeight;
  if (end - top < minHeight) document.body.append(spacer(minHeight - (end - top)));

  // Page bands: page 1 from the top, each break starting the next.
  const edges = [0, ...breaks.map(docY), document.documentElement.scrollHeight];
  const bands = [];
  for (let i = 0; i + 1 < edges.length; i++) {
    bands.push({ page: i + 1, top: edges[i], height: Math.max(1, edges[i + 1] - edges[i]) });
  }
  const bandOf = (y) => {
    for (let i = bands.length - 1; i >= 0; i--) if (y >= bands[i].top - 0.5) return bands[i];
    return bands[0];
  };

  // Visible text with whitespace removed, and where each character came from.
  const chars = [];
  const where = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!el || el.closest("script,style,noscript")) continue;
    if (el.checkVisibility && !el.checkVisibility()) continue;
    const t = n.data;
    for (let i = 0; i < t.length; i++) {
      if (!/\s/.test(t[i])) { chars.push(t[i]); where.push([n, i]); }
    }
  }
  const hay = chars.join("");

  // Pages with any visible text — measured once per text node, by the text
  // itself (its parent element can span pages).
  const withText = new Set();
  const probe = document.createRange();
  let last = null;
  for (const [n] of where) {
    if (n === last) continue;
    last = n;
    probe.selectNodeContents(n);
    const r = probe.getBoundingClientRect();
    if (r.height > 0) withText.add(bandOf(r.top + window.scrollY).page);
  }

  const boxes = {}, missing = [], misplaced = [];
  let cursor = 0;
  for (const p of paragraphs) {
    const needle = p.text.replace(/\s+/g, "");
    if (!needle) { missing.push(p.pid); continue; }
    let at = hay.indexOf(needle, cursor);
    if (at < 0) at = hay.indexOf(needle);
    if (at < 0) { missing.push(p.pid); continue; }
    cursor = at + needle.length;
    const [sn, so] = where[at];
    const [en, eo] = where[at + needle.length - 1];
    const r = document.createRange();
    r.setStart(sn, so);
    r.setEnd(en, eo + 1);
    const rects = [...r.getClientRects()].filter((q) => q.width > 0 && q.height > 0);
    if (!rects.length) { missing.push(p.pid); continue; }
    const x0 = Math.min(...rects.map((q) => q.left)) + window.scrollX;
    const x1 = Math.max(...rects.map((q) => q.right)) + window.scrollX;
    const y0 = Math.min(...rects.map((q) => q.top)) + window.scrollY;
    const y1 = Math.max(...rects.map((q) => q.bottom)) + window.scrollY;
    const band = bandOf(y0);
    if (band.page !== p.page || y1 > band.top + band.height + 0.5) {
      misplaced.push({ pid: p.pid, expected: p.page, found: band.page });
      continue;
    }
    boxes[p.pid] = { page: band.page, x0, y0: y0 - band.top, x1, y1: y1 - band.top };
  }
  return { bands: bands.map((b) => ({ ...b, hasText: withText.has(b.page) })),
           boxes, missing, misplaced };
}
"""


@dataclass(slots=True)
class RenderResult:
    accession: str
    source_format: str
    pages: int = 0
    pages_with_images: int = 0
    paragraphs: int = 0
    boxed: int = 0
    missing: list[str] = field(default_factory=list)
    misplaced: list[dict] = field(default_factory=list)
    pdf_pages: int | None = None
    error: str | None = None


def _paragraphs(chunks: list[Chunk]) -> list[dict[str, Any]]:
    """Every stored paragraph once, in document order, with its page."""
    out, seen = [], set()
    for c in chunks:
        for p in split_chunk(c.text, c.paragraph_ids, c.page_number) or []:
            if p.paragraph_id not in seen and p.page:
                seen.add(p.paragraph_id)
                out.append({"pid": p.paragraph_id, "page": p.page, "text": p.text})
    return out


def _is_styled(html: str) -> bool:
    head = html[:200_000].lower()
    return "<style" in head or "<body style" in head or "stylesheet" in head


def _webp(png: bytes, *, width: int | None = None) -> bytes:
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    if width:
        image.thumbnail((width, 10 * width), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "WEBP", quality=82 if width else 88, method=5)
    return out.getvalue()


def render_html(document: Document, chunks: list[Chunk], browser: Any) -> tuple[RenderResult, dict]:
    """Lay the filing out, measure it, photograph it. Writes files, not rows."""
    result = RenderResult(accession=document.accession, source_format="html")
    html = store.resolve(document.source_path).read_bytes().decode("utf-8", errors="replace")
    paragraphs = _paragraphs(chunks)
    result.paragraphs = len(paragraphs)
    styled = _is_styled(html)

    context = browser.new_context(viewport={"width": PAGE_WIDTH_PX, "height": PAGE_HEIGHT_PX},
                                  device_scale_factor=SCALE, java_script_enabled=True)
    # Nothing leaves the machine: a filing's images and fonts are not fetched.
    context.route("**/*", lambda route: route.abort())
    page = context.new_page()
    try:
        page.set_content(html, wait_until="load")
        if not styled:
            page.add_style_tag(content=BASE_CSS)

        measured = page.evaluate(MEASURE_JS, {
            "paragraphs": paragraphs, "minHeight": PAGE_HEIGHT_PX,
            "topPad": 72, "styled": styled})

        images: dict[int, tuple[str, str]] = {}
        for band in measured["bands"]:
            if not band["hasText"]:
                continue
            png = page.screenshot(full_page=True, type="png", clip={
                "x": 0, "y": band["top"], "width": PAGE_WIDTH_PX, "height": band["height"]})
            full = store.write(store.page_path(document.accession, band["page"]), _webp(png))
            thumb = store.write(store.page_path(document.accession, band["page"], thumbnail=True),
                                _webp(png, width=THUMB_WIDTH))
            images[band["page"]] = (full, thumb)

        # The PDF, from the same layout: printed with screen styles, forced to
        # break only where the filing's pages begin, on pages as tall as the
        # tallest one — so PDF page N is exactly the image of page N. Chrome's
        # own pagination would not be: it came out at 80 pages for an 85-page
        # filing.
        page.add_style_tag(content=PRINT_CSS)
        page.emulate_media(media="screen")
        tallest = max(b["height"] for b in measured["bands"])
        pdf = page.pdf(width=f"{PAGE_WIDTH_PX}px", height=f"{int(tallest) + 1}px",
                       margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                       print_background=True)
        pdf_rel = store.write(store.pdf_path(document.accession), pdf)
        from pypdf import PdfReader
        result.pdf_pages = len(PdfReader(io.BytesIO(pdf)).pages)
    finally:
        context.close()

    result.pages = len(measured["bands"])
    result.pages_with_images = len(images)
    result.boxed = len(measured["boxes"])
    result.missing = measured["missing"]
    result.misplaced = measured["misplaced"]
    return result, {"bands": measured["bands"], "boxes": measured["boxes"],
                    "images": images, "pdf": pdf_rel}


def _page_rows(document: Document, bands: list[dict], images: dict, boxes: dict,
               now: datetime) -> list[DocumentPage]:
    per_page: dict[int, int] = {}
    for b in boxes.values():
        per_page[b["page"]] = per_page.get(b["page"], 0) + 1
    return [DocumentPage(
        document_id=document.id, page=b["page"],
        width=PAGE_WIDTH_PX * PT_PER_PX, height=round(b["height"] * PT_PER_PX, 2),
        image_path=images.get(b["page"], (None, None))[0],
        thumbnail_path=images.get(b["page"], (None, None))[1],
        paragraph_count=per_page.get(b["page"], 0), rendered_at=now) for b in bands]


def _box_json(b: dict, page_height_px: float) -> dict:
    return {"page": b["page"],
            "x0": round(b["x0"] * PT_PER_PX, 2), "y0": round(b["y0"] * PT_PER_PX, 2),
            "x1": round(b["x1"] * PT_PER_PX, 2), "y1": round(b["y1"] * PT_PER_PX, 2),
            "page_width": PAGE_WIDTH_PX * PT_PER_PX,
            "page_height": round(page_height_px * PT_PER_PX, 2)}


def render_document(db, document: Document, browser: Any) -> RenderResult:
    if not document.source_path or not store.resolve(document.source_path).exists():
        return RenderResult(accession=document.accession, source_format=document.source_format,
                            error="no stored source — re-ingest the filing to keep its bytes")
    chunks = list(db.execute(select(Chunk).where(Chunk.document_id == document.id)
                             .order_by(Chunk.ordinal)).scalars())
    now = datetime.now(timezone.utc)
    db.query(DocumentPage).filter(DocumentPage.document_id == document.id).delete(
        synchronize_session=False)

    if document.source_format == "pdf":
        return _render_pdf(db, document, chunks, now)

    if isinstance(browser, BrowserThread):
        result, made = browser.run(render_html, document, chunks)
    else:
        result, made = render_html(document, chunks, browser)
    heights = {b["page"]: b["height"] for b in made["bands"]}
    boxes = {pid: _box_json(b, heights[b["page"]]) for pid, b in made["boxes"].items()}
    for c in chunks:
        mine = {pid: boxes[pid] for pid in (c.paragraph_ids or []) if pid in boxes}
        c.paragraph_boxes = mine or None
    db.add_all(_page_rows(document, made["bands"], made["images"], made["boxes"], now))
    document.pdf_path = made["pdf"]
    document.rendered_at = now
    if result.pages != document.page_count:
        log.warning("%s: rendered %d pages, parser counted %s", document.accession,
                    result.pages, document.page_count)
    return result


def _render_pdf(db, document: Document, chunks: list[Chunk], now: datetime) -> RenderResult:
    """A PDF filing: its boxes came from parsing, its PDF is itself. Page
    sizes are recorded; page images await a rasteriser."""
    from pypdf import PdfReader
    reader = PdfReader(str(store.resolve(document.source_path)))
    counts: dict[int, int] = {}
    boxed = 0
    for c in chunks:
        for box in (c.paragraph_boxes or {}).values():
            counts[box["page"]] = counts.get(box["page"], 0) + 1
            boxed += 1
    db.add_all([DocumentPage(document_id=document.id, page=i,
                             width=float(p.cropbox.width), height=float(p.cropbox.height),
                             paragraph_count=counts.get(i, 0), rendered_at=now)
                for i, p in enumerate(reader.pages, start=1)])
    document.pdf_path = document.source_path
    document.rendered_at = now
    return RenderResult(accession=document.accession, source_format="pdf",
                        pages=len(reader.pages), paragraphs=boxed, boxed=boxed,
                        pdf_pages=len(reader.pages))


class BrowserThread:
    """A browser on a thread of its own.

    Playwright's sync API runs an event loop on the thread that starts it,
    which collides with any asyncio loop already there — an async test, an
    ASGI server. Owning one thread and handing it the work keeps the browser
    out of everyone else's loop. Playwright objects never leave this thread.

        with BrowserThread() as browser:
            render_document(db, document, browser)
    """

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
        self._pool.submit(self._start).result()

    def _start(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = launch_browser(self._pw)

    def run(self, fn, *args):
        """Call `fn(*args, browser)` on the browser's thread."""
        return self._pool.submit(lambda: fn(*args, self.browser)).result()

    def close(self) -> None:
        def stop():
            self.browser.close()
            self._pw.stop()
        self._pool.submit(stop).result()
        self._pool.shutdown()

    def __enter__(self) -> "BrowserThread":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def launch_browser(playwright: Any) -> Any:
    """The installed Chrome if there is one, else Playwright's own Chromium
    (`playwright install chromium`)."""
    try:
        return playwright.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return playwright.chromium.launch(headless=True)


def render(*, ticker: str | None = None, url: str | None = None,
           only_missing: bool = False) -> list[RenderResult]:
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        try:
            with session_scope(url) as db:
                stmt = select(Document).order_by(Document.filed_at)
                if ticker:
                    stmt = stmt.join(Company).where(Company.ticker == ticker.upper())
                if only_missing:
                    stmt = stmt.where(Document.rendered_at.is_(None))
                for document in db.execute(stmt).scalars().all():
                    try:
                        results.append(render_document(db, document, browser))
                        db.flush()
                    except Exception as exc:        # one bad filing must not sink the batch
                        log.exception("failed on %s", document.accession)
                        results.append(RenderResult(accession=document.accession,
                                                    source_format=document.source_format,
                                                    error=str(exc)))
        finally:
            browser.close()
    return results


if __name__ == "__main__":
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Render filings to pages, images and boxes.")
    ap.add_argument("ticker", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only-missing", action="store_true")
    args = ap.parse_args()
    if not args.ticker and not args.all:
        ap.error("name a ticker, or --all")
    results = render(ticker=args.ticker, only_missing=args.only_missing)
    for r in results:
        state = f"error: {r.error}" if r.error else (
            f"{r.pages} pages ({r.pages_with_images} with text), "
            f"{r.boxed}/{r.paragraphs} paragraphs boxed, PDF {r.pdf_pages} pages"
            + (f", {len(r.missing)} not found" if r.missing else "")
            + (f", {len(r.misplaced)} on the wrong page" if r.misplaced else ""))
        print(f"  {r.accession} [{r.source_format}] {state}")
    sys.exit(1 if any(r.error for r in results) else 0)
