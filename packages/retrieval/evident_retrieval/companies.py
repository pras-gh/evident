"""Which companies on record a search names — by rule, without a model.

Vector search ranks every passage on record by meaning, so on its own it will
answer "Wesco International" with the nearest NVIDIA paragraph. Search has to
know which company is being asked about before it ranks anything. This is the
half of that which needs no model: tickers and names of the companies we hold
filings for, matched in the query text. It always runs, costs nothing, and is
what search falls back on when the model that reads the query is unavailable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+")

#: Words that end a company's legal name and are left out of how people say
#: it: "NVIDIA CORP" is "Nvidia", "Wesco International Inc" is "Wesco
#: International".
_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "plc", "llc", "lp", "holdings", "holding", "group", "sa",
    "nv", "ag", "se", "the", "de", "del", "class", "a", "b", "com",
})

#: Tickers that are also ordinary words. Matched only when written as a ticker
#: — in capitals, or with a `$` — so "is it on" does not name IT and ON.
_WORDLIKE = frozenset({
    "a", "all", "am", "an", "and", "are", "as", "at", "be", "big", "by", "can",
    "car", "cat", "do", "for", "fun", "go", "has", "he", "hi", "how", "if", "in",
    "is", "it", "key", "low", "me", "new", "now", "of", "on", "one", "or", "out",
    "run", "see", "so", "some", "the", "to", "two", "up", "us", "very", "was",
    "we", "well", "what", "who", "why", "win", "you",
})


@dataclass(frozen=True, slots=True)
class OnRecord:
    """A company we hold filings for."""
    ticker: str
    name: str
    #: its SEC registration, which outlives a ticker and is shared by its
    #: share classes (GOOGL and GOOG are one CIK)
    cik: str | None = None

    @property
    def short_name(self) -> str:
        return short_name(self.name)


def short_name(name: str) -> str:
    """How people say a company's name: lower case, punctuation gone, and the
    legal suffixes dropped. "NVIDIA CORP" → "nvidia"."""
    words = _WORD.findall(name.lower())
    while words and words[-1] in _SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def _says(text_words: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in text_words


def _ticker_in(query: str, ticker: str) -> bool:
    for m in re.finditer(rf"(?<![\w$])(\$?){re.escape(ticker)}(?![\w])", query, re.I):
        written = m.group(0)
        if (m.group(1) or written == ticker.upper()
                or ticker.lower() not in _WORDLIKE):
            return True
    return False


def named(query: str, on_record: list[OnRecord]) -> list[OnRecord]:
    """The companies on record that `query` names, by ticker ("nvda", "$NVDA")
    or by name ("Nvidia", "NVIDIA Corp's"), in the order given."""
    words = f" {' '.join(_WORD.findall(query.lower()))} "
    return [c for c in on_record
            if _ticker_in(query, c.ticker) or _says(words, c.short_name)]


def same_company(name: str, ticker: str | None, c: OnRecord) -> bool:
    """Whether a company a model named is `c`: by ticker, or by name."""
    if ticker and ticker.strip().upper().lstrip("$") == c.ticker.upper():
        return True
    said = short_name(name)
    return bool(said) and (said == c.short_name or said == c.ticker.lower())
