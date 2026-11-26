"""Alerts for new filings: what the sync worker says when a tracked company files.

Every alert is logged. With `ALERT_WEBHOOK_URL` set, each is also POSTed
there as JSON: `text` (one line, which Slack's incoming webhooks display) and
`filing` (the fields, for a receiver of your own). Delivery is best effort:
a webhook that is down never stops a sync, and a filing whose alert was not
delivered keeps `alerted_at` null.

`ALERT_FORMS` (default "10-K,10-Q,8-K,20-F,40-F,6-K") limits which forms alert.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime

from parallx_parser import edgar
from parallx_parser import forms as forms_

log = logging.getLogger("parallx.alerts")

@dataclass(frozen=True, slots=True)
class NewFiling:
    ticker: str
    company: str
    cik: str
    accession: str
    form_type: str
    accepted_at: datetime
    report_date: date | None = None
    items: str | None = None
    primary_document: str | None = None


def forms() -> set[str]:
    raw = os.environ.get("ALERT_FORMS", "10-K,10-Q,8-K,20-F,40-F,6-K")
    return {f.strip().upper() for f in raw.split(",") if f.strip()}


def about(f: NewFiling) -> str:
    """What a filing is, in words: ": results of operations", or " for the
    period ended 2026-10-26"."""
    said = forms_.about(f.form_type, f.items, f.report_date)
    if not said:
        return ""
    return f": {said}" if f.form_type.startswith(("8-K", "6-K")) else f" {said}"


def text(f: NewFiling) -> str:
    name = forms_.FORM_NAMES.get(f.form_type, "filing")
    article = "an" if f.form_type[0] in "8AEFHILMNORSX" else "a"
    return f"{f.ticker} filed {article} {f.form_type} ({name}){about(f)}"


def link(f: NewFiling) -> str:
    return edgar.public_url(f.cik, f.accession)


def send(filings: list[NewFiling], *, post=None) -> list[str]:
    """Alerts for `filings` of the alerting forms; returns the accessions
    whose alert went out (logged, and delivered if a webhook is set)."""
    url = os.environ.get("ALERT_WEBHOOK_URL")
    wanted = forms()
    sent: list[str] = []
    for f in filings:
        if f.form_type not in wanted:
            continue
        line = text(f)
        log.info("alert: %s — %s", line, link(f))
        if url:
            try:
                (post or _post)(url, {
                    "text": f"{line} <{link(f)}|on EDGAR>",
                    "filing": {"ticker": f.ticker, "company": f.company, "cik": f.cik,
                               "accession": f.accession, "form_type": f.form_type,
                               "accepted_at": f.accepted_at.isoformat(),
                               "items": f.items, "url": link(f)},
                })
            except Exception as exc:          # alerting never stops a sync
                log.warning("alert for %s not delivered: %s", f.accession, str(exc)[:200])
                continue
        sent.append(f.accession)
    return sent


def _post(url: str, body: dict) -> None:
    import httpx
    httpx.post(url, json=body, timeout=10).raise_for_status()
