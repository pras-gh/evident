"""Sentence boundaries in filing prose, for turning a quoted span into the
sentence that contains it.

Filings are full of periods that do not end sentences — "U.S.", "Inc.",
"No. 1", "e.g." — and a quote cut at one ("…and foreign government bodies")
reads as a broken citation. A boundary is a `.`, `!` or `?` (and any closing
quote or bracket), then whitespace, then something that can start a sentence,
unless the word before the period is a known abbreviation or an initial.
"""
from __future__ import annotations

import re

_END = re.compile(r"[.!?][\"'”’)\]]*\s+(?=[A-Z0-9\"“‘(\[])")

#: Lower-cased, without their final period.
ABBREVIATIONS = frozenset({
    "u.s", "u.k", "e.u", "inc", "corp", "co", "ltd", "llc", "plc", "no", "nos",
    "e.g", "i.e", "etc", "vs", "approx", "mr", "ms", "mrs", "dr", "st", "jr",
    "sr", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "fig", "sec", "art",
})

_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)$")


def _is_abbreviation(text: str, period: int) -> bool:
    """Whether the `.` at `period` closes an abbreviation or an initial."""
    if text[period] != ".":
        return False
    m = _LAST_WORD.search(text, 0, period)
    if not m:
        return False
    word = m.group(1).lower().rstrip(".")
    return word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def bounds(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence, without surrounding whitespace."""
    out, start = [], 0
    for m in _END.finditer(text):
        if _is_abbreviation(text, m.start()):
            continue
        out.append(_trim(text, start, m.end()))
        start = m.end()
    out.append(_trim(text, start, len(text)))
    return [(s, e) for s, e in out if e > s]


def around(text: str, start: int, end: int) -> tuple[int, int]:
    """The span of the sentence or sentences containing `text[start:end]`."""
    spans = bounds(text)
    first = next((s for s, e in spans if s <= start < e), start)
    last = next((e for s, e in reversed(spans) if s < end <= e), end)
    return first, last
