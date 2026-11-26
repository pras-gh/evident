"""SEC form types and 8-K items, in the words an investor would use."""
from __future__ import annotations

from datetime import date

FORM_NAMES = {
    "10-K": "annual report", "10-K/A": "amended annual report",
    "10-Q": "quarterly report", "10-Q/A": "amended quarterly report",
    "8-K": "current report", "8-K/A": "amended current report",
    "20-F": "annual report", "40-F": "annual report", "6-K": "current report",
}

#: 8-K items, as an investor would name them. 9.01 (exhibits) goes with
#: nearly every 8-K, so it says nothing on its own.
ITEMS_8K = {
    "1.01": "a material agreement", "1.02": "the end of a material agreement",
    "1.03": "bankruptcy", "1.05": "a cybersecurity incident",
    "2.01": "an acquisition or disposition", "2.02": "results of operations",
    "2.03": "a new financial obligation", "2.05": "restructuring costs",
    "2.06": "an impairment", "3.01": "a listing notice", "3.02": "an unregistered sale of equity",
    "4.01": "a change of auditor", "4.02": "non-reliance on past financial statements",
    "5.02": "a change of directors or officers", "5.03": "amended articles or bylaws",
    "5.07": "shareholder vote results", "7.01": "a Regulation FD disclosure",
    "8.01": "other events",
}


def items_said(items: str | None) -> list[str]:
    """An 8-K's items in words; 9.01 (exhibits), which goes with nearly every
    8-K, is left out."""
    return [ITEMS_8K[i.strip()] for i in (items or "").split(",") if i.strip() in ITEMS_8K]


def about(form_type: str, items: str | None, report_date: date | None) -> str | None:
    """What a filing is about: "results of operations" for an 8-K with item
    2.02, "for the period ended 2026-10-26" for a periodic report."""
    if form_type.startswith(("8-K", "6-K")):
        return ", ".join(items_said(items)) or None
    if report_date:
        return f"for the period ended {report_date.isoformat()}"
    return None
