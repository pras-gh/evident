#!/usr/bin/env python3
"""History from EDGAR's nightly bulk ZIPs: every tracked company's whole filing
list and every financial statement it has filed, without a request per company.

    python tools/sec_bulk.py download             # submissions.zip and companyfacts.zip → data/bulk/
    python tools/sec_bulk.py load                 # the tracked companies, from those ZIPs
    python tools/sec_bulk.py load --only NVDA,AAPL

For backtesting: the API path (`workers/sync_worker.py`) keeps the ~1,000
most recent filings per company and is enough to run on; this adds the older
pages too (`--history` is implied), from the same files EDGAR builds nightly.
Each ZIP is over a gigabyte; `load` reads only the tracked companies' entries
from it, without unpacking the rest.

Both steps need SEC_USER_AGENT (download) and DATABASE_URL (load).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
for pkg in ("db", "parser", "memory", "retrieval", "graph", "ai"):
    sys.path.insert(0, str(ROOT / "packages" / pkg))
sys.path.insert(0, str(ROOT))

from parallx_parser import edgar  # noqa: E402
from parallx_parser.universe import load as tracked  # noqa: E402

DIR = ROOT / "data" / "bulk"
log = logging.getLogger("parallx.sec_bulk")


def download(kind: str, out_dir: pathlib.Path = DIR) -> pathlib.Path:
    """Streams one bulk file to `out_dir`, replacing it only once complete."""
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT — EDGAR refuses anonymous automated traffic")
    out_dir.mkdir(parents=True, exist_ok=True)
    target, part = out_dir / f"{kind}.zip", out_dir / f"{kind}.zip.part"
    req = urllib.request.Request(edgar.bulk_url(kind), headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if total and done % (100 << 20) < (1 << 20):
                log.info("%s: %d of %d MB", kind, done >> 20, total >> 20)
    part.replace(target)
    log.info("%s: %d MB → %s", kind, done >> 20, target.relative_to(ROOT))
    return target


def _read(z: zipfile.ZipFile | None, name: str) -> dict | None:
    if z is None:
        return None
    try:
        with z.open(name) as fh:
            return json.load(fh)
    except KeyError:
        return None


def load(zip_dir: pathlib.Path = DIR, *, only: set[str] | None = None,
         url: str | None = None) -> dict[str, int]:
    """Every tracked company (or `only` those tickers) from the ZIPs in
    `zip_dir`: whole filing list, and statements."""
    from workers.sync_worker import refresh
    subs = zip_dir / "submissions.zip"
    facts = zip_dir / "companyfacts.zip"
    if not subs.exists():
        raise SystemExit(f"no {subs.relative_to(ROOT)} — run: python tools/sec_bulk.py download")
    out = {"companies": 0, "filings": 0, "facts": 0, "missing": 0}
    with zipfile.ZipFile(subs) as zs, \
            (zipfile.ZipFile(facts) if facts.exists() else _Null()) as zf:
        for t in tracked():
            if only and not only & set(t.tickers):
                continue
            submission = _read(zs, f"CIK{t.cik}.json")
            if submission is None:
                log.warning("%s: not in submissions.zip", t.ticker)
                out["missing"] += 1
                continue
            r = refresh(url, t, history=True, pages=lambda name: _read(zs, name) or {},
                        submission=submission, facts_payload=_read(zf, f"CIK{t.cik}.json"),
                        download=False)
            out["companies"] += 1
            out["filings"] += len(r.new)
            out["facts"] += r.facts
            log.info("%s: %d filings, %d facts", r.ticker, len(r.new), r.facts)
    return out


class _Null:
    """No companyfacts.zip: statements then come from the API, per company."""
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("kinds", nargs="*", default=["submissions", "companyfacts"],
                   choices=["submissions", "companyfacts"])
    lo = sub.add_parser("load")
    lo.add_argument("--dir", type=pathlib.Path, default=DIR)
    lo.add_argument("--only", help="tickers, comma-separated")
    args = ap.parse_args()
    if args.cmd == "download":
        for kind in args.kinds:
            download(kind)
    else:
        only = {x.strip().upper() for x in args.only.split(",")} if args.only else None
        print(json.dumps(load(args.dir, only=only)))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
