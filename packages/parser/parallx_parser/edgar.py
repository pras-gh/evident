"""Minimal EDGAR client.

Only what ingestion needs: resolve an accession to its primary document and its
*acceptance* timestamp, and list what was filed lately. The acceptance
datetime is the one that matters — it is when the filing became public, which
is what a reader means by "when did they say this". `filed_date` is a date
only, and fetch time is not evidence of anything.

SEC requires a descriptive User-Agent with contact details; requests without
one are throttled or refused.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# Overridable so the fetch layer can point at a cache, a corporate proxy, or a
# fixture server. Plenty of environments cannot reach sec.gov directly — SEC
# blocks whole IP ranges at the edge — and a hard-coded origin makes those
# environments untestable rather than merely inconvenient.
BASE = os.environ.get("SEC_DATA_URL", "https://data.sec.gov").rstrip("/")
ARCHIVES = os.environ.get(
    "SEC_ARCHIVES_URL", "https://www.sec.gov/Archives/edgar/data").rstrip("/")
WWW = os.environ.get("SEC_WWW_URL", "https://www.sec.gov").rstrip("/")
_UA_ENV = "SEC_USER_AGENT"
_MIN_INTERVAL = 0.11          # SEC asks for <= 10 requests/second
_last_call = 0.0


def _get(url: str) -> bytes:
    global _last_call
    ua = os.environ.get(_UA_ENV)
    if not ua:
        raise RuntimeError(
            f"Set {_UA_ENV} to something like 'Parallx ingest (you@example.com)'. "
            "SEC refuses anonymous automated traffic."
        )
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": ua,
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").strip().lower()
    _last_call = time.monotonic()
    return decode(data, encoding)


def decode(data: bytes, encoding: str) -> bytes:
    """The body as sent, before compression. This client asks for gzip, and
    sec.gov answers with it; read raw, the company list does not parse as
    JSON, a filing parses as garbage, and the content digest is of the
    compressed bytes. (Only the uncompressed fixture server had been used.)"""
    if encoding == "gzip":
        return gzip.decompress(data)
    if encoding == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:                       # raw deflate, without a header
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def has_user_agent() -> bool:
    """Whether EDGAR can be asked anything: it refuses requests without a
    contact in the User-Agent."""
    return bool(os.environ.get(_UA_ENV))


@dataclass(frozen=True, slots=True)
class SecCompany:
    """A row of EDGAR's company list. A company with two share classes has a
    row per ticker (GOOGL, GOOG), both with its one CIK."""
    cik: str
    ticker: str
    name: str


#: EDGAR's company list, fetched once per process: ~1MB, changes rarely, and
#: re-fetching it per request would burn the rate limit on data we already
#: have. Set it to None to fetch it again (tests switching origins do).
_TICKER_MAP: list[SecCompany] | None = None


def companies() -> list[SecCompany]:
    global _TICKER_MAP
    if _TICKER_MAP is None:
        raw = json.loads(_get(f"{WWW}/files/company_tickers.json"))
        _TICKER_MAP = [SecCompany(str(row["cik_str"]).zfill(10), row["ticker"].upper(),
                                  row.get("title") or row["ticker"].upper())
                       for row in raw.values()]
    return _TICKER_MAP


def cik_for_ticker(ticker: str) -> str:
    """Resolve a ticker to a zero-padded CIK via EDGAR's own mapping file."""
    found = lookup(ticker=ticker)
    if found is None:
        raise LookupError(f"{ticker.upper()} is not in EDGAR's ticker map")
    return found.cik


_WORDS = re.compile(r"[a-z0-9]+")
_SUFFIXES = frozenset({"inc", "incorporated", "corp", "corporation", "co", "company",
                       "ltd", "limited", "plc", "llc", "lp", "holdings", "group", "sa",
                       "nv", "ag", "se", "the", "class", "a", "b", "c", "com", "de"})


def _plain(name: str) -> str:
    words = _WORDS.findall(name.lower())
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(w for w in words if w != "the")


def lookup(*, ticker: str | None = None, name: str | None = None) -> SecCompany | None:
    """A company on EDGAR, by ticker ("GOOGL", "$googl") or, failing that, by
    name as people say it ("Wesco International" for WESCO INTERNATIONAL INC).
    None when EDGAR has no such filer — a private company, or a foreign one
    that does not file with the SEC."""
    rows = companies()
    if ticker:
        t = ticker.strip().upper().lstrip("$")
        hit = next((c for c in rows if c.ticker == t), None)
        if hit:
            return hit
    if name:
        plain = _plain(name)
        if plain:
            return next((c for c in rows if _plain(c.name) == plain), None)
    return None


def recent_filings(submission: dict[str, Any], *, form_types: list[str] | None = None,
                   limit: int = 5) -> list[dict[str, Any]]:
    """The most recent filings, newest first, optionally filtered by form."""
    recent = submission["filings"]["recent"]
    out: list[dict[str, Any]] = []
    for i, form in enumerate(recent["form"]):
        if form_types and form not in form_types:
            continue
        out.append({
            "accession": recent["accessionNumber"][i],
            "form_type": form,
            "filed_date": date.fromisoformat(recent["filingDate"][i]),
            "published_at": _acceptance(recent, i),
            "primary_document": recent["primaryDocument"][i],
            "report_date": (recent.get("reportDate", []) or [None])[i]
                           if i < len(recent.get("reportDate", [])) else None,
        })
        if len(out) >= limit:
            break
    return out


def normalise_accession(accession: str) -> tuple[str, str]:
    """Return (dashed, bare) forms — EDGAR paths need the bare one."""
    bare = accession.replace("-", "")
    dashed = f"{bare[:10]}-{bare[10:12]}-{bare[12:]}"
    return dashed, bare


def fetch_submission(cik: str) -> dict[str, Any]:
    return json.loads(_get(f"{BASE}/submissions/CIK{cik.zfill(10)}.json"))


def find_filing(submission: dict[str, Any], accession: str) -> dict[str, Any]:
    """Pull one filing's metadata out of EDGAR's column-oriented payload."""
    dashed, _ = normalise_accession(accession)
    recent = submission["filings"]["recent"]
    try:
        i = recent["accessionNumber"].index(dashed)
    except ValueError as exc:
        raise LookupError(f"{dashed} not in the recent filings for this CIK") from exc
    return {
        "accession": dashed,
        "form_type": recent["form"][i],
        "filed_date": date.fromisoformat(recent["filingDate"][i]),
        "published_at": _acceptance(recent, i),
        "primary_document": recent["primaryDocument"][i],
        "report_date": recent.get("reportDate", [None] * (i + 1))[i] or None,
    }


def _acceptance(recent: dict[str, Any], i: int) -> datetime:
    raw = recent.get("acceptanceDateTime", [None] * (i + 1))[i]
    if raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # Fall back to midnight on the filing date rather than inventing a time.
    return datetime.fromisoformat(recent["filingDate"][i] + "T00:00:00+00:00")


@dataclass(frozen=True, slots=True)
class LatestFiling:
    """One entry of EDGAR's latest-filings feed."""
    cik: str
    accession: str
    form_type: str
    company: str
    #: when EDGAR accepted it, as the feed reports it
    accepted_at: datetime | None


_FEED_NS = {"a": "http://www.w3.org/2005/Atom"}
_FEED_TITLE = re.compile(r"^(?P<form>\S+) - (?P<name>.*) \((?P<cik>\d{10})\)")
_FEED_ACCESSION = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")


def latest_filings(form_type: str, *, count: int = 100, start: int = 0) -> list[LatestFiling]:
    """The newest filings of one form across every filer, newest first, from
    EDGAR's latest-filings feed — the same list as sec.gov's "Latest Filings"
    page, updated as EDGAR accepts filings. One request answers "what's new
    since I last looked" for every company at once."""
    from urllib.parse import quote
    url = (f"{WWW}/cgi-bin/browse-edgar?action=getcurrent&type={quote(form_type)}"
           f"&company=&dateb=&owner=include&start={start}&count={count}&output=atom")
    return parse_latest(_get(url), form_type=form_type)


def parse_latest(feed: bytes, *, form_type: str | None = None) -> list[LatestFiling]:
    """The feed's entries for `form_type` exactly. EDGAR's `type` filter is a
    prefix (10-K also returns 10-K/A and 10-KT); amendments are left out."""
    import xml.etree.ElementTree as ET
    out: list[LatestFiling] = []
    for entry in ET.fromstring(feed).findall("a:entry", _FEED_NS):
        title = (entry.findtext("a:title", "", _FEED_NS) or "").strip()
        m = _FEED_TITLE.match(title)
        acc = _FEED_ACCESSION.search(entry.findtext("a:id", "", _FEED_NS) or "")
        if not m or not acc:
            continue
        cat = entry.find("a:category", _FEED_NS)
        form = (cat.get("term") if cat is not None else None) or m["form"]
        if form_type and form != form_type:
            continue
        updated = entry.findtext("a:updated", "", _FEED_NS)
        try:
            accepted = datetime.fromisoformat(updated) if updated else None
        except ValueError:
            accepted = None
        out.append(LatestFiling(cik=m["cik"], accession=acc[1], form_type=form,
                                company=m["name"].strip(), accepted_at=accepted))
    return out


def document_url(cik: str, accession: str, primary_document: str) -> str:
    _, bare = normalise_accession(accession)
    return f"{ARCHIVES}/{int(cik)}/{bare}/{primary_document}"


def fetch_document(url: str) -> tuple[bytes, str]:
    """Return (bytes, sha256). The digest lets a re-ingest skip unchanged bytes."""
    raw = _get(url)
    return raw, hashlib.sha256(raw).hexdigest()
