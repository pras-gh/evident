"""Minimal EDGAR client.

Only what ingestion needs: resolve an accession to its primary document and its
*acceptance* timestamp. The acceptance datetime is the one that matters — it is
when the filing became public, which is what a reader means by "when did they
say this". `filed_date` is a date only, and fetch time is not evidence of
anything.

SEC requires a descriptive User-Agent with contact details; requests without
one are throttled or refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import date, datetime
from typing import Any

BASE = "https://data.sec.gov"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_UA_ENV = "SEC_USER_AGENT"
_MIN_INTERVAL = 0.11          # SEC asks for <= 10 requests/second
_last_call = 0.0


def _get(url: str) -> bytes:
    global _last_call
    ua = os.environ.get(_UA_ENV)
    if not ua:
        raise RuntimeError(
            f"Set {_UA_ENV} to something like 'Elevate ingest (you@example.com)'. "
            "SEC refuses anonymous automated traffic."
        )
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": ua,
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    _last_call = time.monotonic()
    return data


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


def document_url(cik: str, accession: str, primary_document: str) -> str:
    _, bare = normalise_accession(accession)
    return f"{ARCHIVES}/{int(cik)}/{bare}/{primary_document}"


def fetch_document(url: str) -> tuple[bytes, str]:
    """Return (bytes, sha256). The digest lets a re-ingest skip unchanged bytes."""
    raw = _get(url)
    return raw, hashlib.sha256(raw).hexdigest()
