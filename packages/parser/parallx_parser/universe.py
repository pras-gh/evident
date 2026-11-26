"""The companies Parallx keeps current: the S&P 500, from config/sp500.csv.

One `Tracked` per company, not per ticker. The index lists Alphabet twice
(GOOGL, GOOG), and News Corp and Fox the same way; they file once, under one
CIK, so they are fetched once. `tickers` keeps every class. The ticker a
company is shown under is EDGAR's (GOOGL), set when its filings are fetched.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "sp500.csv"
_CLASS = re.compile(r"\s*\(Class [A-Z]\)$")


@dataclass(frozen=True, slots=True)
class Tracked:
    cik: str
    name: str
    tickers: tuple[str, ...]
    sector: str = ""

    @property
    def ticker(self) -> str:
        return self.tickers[0]


def load(path: str | Path | None = None) -> list[Tracked]:
    """The tracked companies, in ticker order. `SYNC_UNIVERSE` overrides the
    path (tests point it at a small list)."""
    path = Path(path or os.environ.get("SYNC_UNIVERSE") or DEFAULT_PATH)
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(line for line in fh if not line.startswith("#")))
    by_cik: dict[str, dict] = {}
    for row in rows:
        cik = row["cik"].strip().zfill(10)
        ticker = row["ticker"].strip().upper()
        if cik in by_cik:
            by_cik[cik]["tickers"].append(ticker)
        else:
            by_cik[cik] = {"name": _CLASS.sub("", row["name"].strip()), "tickers": [ticker],
                           "sector": (row.get("sector") or "").strip()}
    out = [Tracked(cik=cik, name=v["name"], tickers=tuple(v["tickers"]), sector=v["sector"])
           for cik, v in by_cik.items()]
    return sorted(out, key=lambda t: t.ticker)
