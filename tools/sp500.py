#!/usr/bin/env python3
"""Refresh config/sp500.csv, the S&P 500 as Parallx tracks it.

    python tools/sp500.py            # fetch, write, and show what changed
    python tools/sp500.py --check    # fetch and show what changed; write nothing

The list comes from Wikipedia's "List of S&P 500 companies" (CC BY-SA 4.0),
whose constituents table carries each company's SEC CIK. The CIK is what the
sync worker keys on: tickers change and share classes repeat (GOOGL and GOOG
are one company, one CIK, one set of filings), a CIK does neither.

The index changes a few times a quarter. Run this when it does; the sync
worker picks the new list up on its next cycle and backfills whoever joined.
"""
from __future__ import annotations

import argparse
import csv
import io
import pathlib
import sys
import urllib.request
from datetime import date
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "sp500.csv"
SOURCE = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Wikipedia asks automated clients to say who they are; no personal contact.
UA = "Parallx/0.1 (https://ll.ax) sp500-list-refresh"
COLUMNS = ["ticker", "cik", "name", "sector", "sub_industry", "date_added"]


class _Table(HTMLParser):
    """The rows of <table id="constituents">, as lists of cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._depth = 0          # table nesting inside the constituents table
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._depth or dict(attrs).get("id") == "constituents":
                self._depth += 1
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse(html: str, *, expected: range = range(495, 511)) -> list[dict[str, str]]:
    """The constituents, one dict per ticker, sorted by ticker. Raises if the
    table is missing, its columns moved, or it holds a number of rows the
    S&P 500 does not (about 503 tickers), rather than write a broken list."""
    t = _Table()
    t.feed(html)
    if not t.rows:
        raise ValueError("no table with id 'constituents' on the page")
    head = [h.lower() for h in t.rows[0]]
    want = {"symbol": "ticker", "security": "name", "gics sector": "sector",
            "gics sub-industry": "sub_industry", "date added": "date_added", "cik": "cik"}
    missing = [w for w in want if w not in head]
    if missing:
        raise ValueError(f"the constituents table has no {', '.join(missing)} column: {head}")
    idx = {want[w]: head.index(w) for w in want}
    out = []
    for row in t.rows[1:]:
        if len(row) < len(head):
            continue
        rec = {k: row[i].strip() for k, i in idx.items()}
        # Wikipedia writes share classes with a dot (BRK.B); EDGAR, a dash.
        rec["ticker"] = rec["ticker"].upper().replace(".", "-")
        if not rec["cik"].isdigit():
            raise ValueError(f"{rec['ticker']}: CIK {rec['cik']!r} is not a number")
        rec["cik"] = rec["cik"].zfill(10)
        out.append(rec)
    if len(out) not in expected:
        raise ValueError(f"{len(out)} constituents — the S&P 500 has about 503 tickers; "
                         "the page has probably changed shape")
    return sorted(out, key=lambda r: r["ticker"])


def render(rows: list[dict[str, str]], *, as_of: date) -> str:
    buf = io.StringIO()
    buf.write(f"# S&P 500 constituents, as of {as_of.isoformat()}. Source: {SOURCE}\n"
              "# (CC BY-SA 4.0). Regenerate with: python tools/sp500.py\n")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="show changes, write nothing")
    args = ap.parse_args()

    req = urllib.request.Request(SOURCE, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = parse(resp.read().decode("utf-8"))

    before = set()
    if OUT.exists():
        sys.path.insert(0, str(ROOT / "packages" / "parser"))
        from parallx_parser.universe import load
        before = {c.ticker for c in load(OUT)}
    after = {r["ticker"] for r in rows}
    joined, left = sorted(after - before), sorted(before - after)
    print(f"{len(rows)} tickers, {len({r['cik'] for r in rows})} companies")
    if before:
        print(f"joined: {', '.join(joined) or 'none'}")
        print(f"left:   {', '.join(left) or 'none'}")
    if not args.check:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(render(rows, as_of=date.today()))
        print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
