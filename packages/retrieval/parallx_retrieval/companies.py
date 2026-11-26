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
#: — in capitals, or with a `$` — so "is it on" does not name IT and ON, and
#: "cost of revenue" does not name Costco. (Two-letter tickers are matched
#: only that way anyway.) The S&P 500's, and the short words people type.
_WORDLIKE = frozenset({
    "a", "all", "am", "an", "and", "are", "as", "at", "be", "big", "by", "can",
    "car", "cat", "do", "for", "fun", "go", "has", "he", "hi", "how", "if", "in",
    "is", "it", "key", "low", "me", "new", "now", "of", "on", "one", "or", "out",
    "run", "see", "so", "some", "the", "to", "two", "up", "us", "very", "was",
    "we", "well", "what", "who", "why", "win", "you",
    "amp", "app", "ball", "coin", "cop", "cost", "dash", "day", "deck",
    "dis", "doc", "dow", "echo", "fang", "fast", "fix", "flex", "fox", "gen",
    "hood", "hum", "ice", "lite", "mar", "met", "peak", "peg", "pep",
    "pool", "reg", "tap", "tech", "tel",
})

#: Tickers whose capitals usually mean something else: "the DOW" is the
#: index, "FANG stocks" a basket. Only `$DOW` and `$FANG` name the companies.
_ACRONYMS = frozenset({"dow", "fang"})


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


def _shouting(query: str) -> bool:
    """Written all in capitals, where capitals say nothing about tickers."""
    words = re.findall(r"[A-Za-z]+", query)
    return len(words) >= 3 and not any(c.islower() for c in query)


def _ticker_in(query: str, ticker: str, *, explicit_only: bool = False) -> bool:
    """Whether `query` writes `ticker`. With `$` it always does. Otherwise a
    one-letter ticker never does — "A big drop", the t of "don't" — and the
    rest do when written in capitals ("IT spending", "GE"), or, unless
    `explicit_only`, in any case when three letters or more and not a word
    ("nvda", but not "cost" or "fast")."""
    caps_mean_ticker = not _shouting(query)
    for m in re.finditer(rf"(?<![\w$])(\$?){re.escape(ticker)}(?![\w])", query, re.I):
        if m.group(1):
            return True
        if len(ticker) == 1:
            continue
        if (caps_mean_ticker and m.group(0) == ticker.upper()
                and ticker.lower() not in _ACRONYMS):
            return True
        if (not explicit_only and len(ticker) >= 3
                and ticker.lower() not in _WORDLIKE):
            return True
    return False


def named(query: str, on_record: list[OnRecord], *,
          explicit_only: bool = False) -> list[OnRecord]:
    """The companies on record that `query` names, by ticker ("nvda", "$NVDA")
    or by name ("Nvidia", "NVIDIA Corp's"), in the order given.

    `explicit_only` keeps only what cannot be an ordinary word: a `$TICKER`,
    or a ticker in capitals. Search asks for that when a model has read the
    query and named its companies. With the S&P 500 on record, plain words
    are names — Target, News Corp, Block, Ball, Costco's COST — and "the
    latest news on tesla" is not about News Corp.
    """
    words = f" {' '.join(_WORD.findall(query.lower()))} "
    return [c for c in on_record
            if _ticker_in(query, c.ticker, explicit_only=explicit_only)
            or (not explicit_only and _says(words, c.short_name))]


def same_company(name: str, ticker: str | None, c: OnRecord) -> bool:
    """Whether a company a model named is `c`: by ticker, or by name."""
    if ticker and ticker.strip().upper().lstrip("$") == c.ticker.upper():
        return True
    said = short_name(name)
    return bool(said) and (said == c.short_name or said == c.ticker.lower())
