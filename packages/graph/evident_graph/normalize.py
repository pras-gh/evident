"""Topic normalization.

Two jobs. Turn a label into a stable key, and fold the variants a corpus uses
for one thing into a single entity.

The second matters more than it looks. A filing will say "AI infrastructure",
"artificial intelligence infrastructure" and "A.I. infrastructure" in the same
document. Without folding, the graph shows three weak nodes instead of one
strong one, and importance scoring — which is mostly frequency — reports all
three as unimportant.
"""
from __future__ import annotations

import re

_PUNCT = re.compile(r"[^a-z0-9]+")
_ARTICLES = {"the", "a", "an", "our", "its", "their"}

# Expanded before keying, so "A.I." and "AI" land on the same node.
_EXPANSIONS = {
    "ai": "artificial intelligence",
    "a i": "artificial intelligence",
    "ml": "machine learning",
    "r d": "research and development",
    "capex": "capital expenditure",
    "opex": "operating expenditure",
    "ip": "intellectual property",
    "us": "united states",
    "usg": "united states government",
    "ev": "electric vehicle",
    "iot": "internet of things",
}

# Whole-label aliases: distinct wordings that mean one thing.
_ALIASES = {
    "artificial intelligence infrastructure": "ai_infrastructure",
    "artificial intelligence infra": "ai_infrastructure",
    "data center infrastructure": "ai_infrastructure",
    "data centre infrastructure": "ai_infrastructure",
    "accelerated computing": "ai_infrastructure",
    "export control": "export_controls",
    "export restrictions": "export_controls",
    "china export restrictions": "export_controls",
    "capital expenditure": "capex",
    "share repurchase": "buybacks",
    "share repurchases": "buybacks",
    "stock repurchase program": "buybacks",
}


def _words(label: str) -> list[str]:
    tokens = _PUNCT.sub(" ", label.casefold()).split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        pair = " ".join(tokens[i:i + 2])
        if pair in _EXPANSIONS:
            out.extend(_EXPANSIONS[pair].split())
            i += 2
            continue
        token = tokens[i]
        out.extend(_EXPANSIONS.get(token, token).split())
        i += 1
    return [w for w in out if w not in _ARTICLES]


def entity_key(label: str) -> str:
    """A stable, readable key. `AI Infrastructure` -> `ai_infrastructure`.

    Underscores rather than hyphens because the graph contract's node ids use
    them, and node ids are what clients hold onto.
    """
    words = _words(label)
    phrase = " ".join(words)
    if phrase in _ALIASES:
        return _ALIASES[phrase]
    # collapse the expansion back down for readability in the key
    key = "_".join(words)
    key = key.replace("artificial_intelligence", "ai")
    key = key.replace("research_and_development", "rd")
    return key or "unknown"


# When several labels fold to one key, the reader should not see whichever
# variant happened to be ingested last. Keys that exist because of an alias get
# a fixed display name.
_KEY_LABELS = {
    "ai_infrastructure": "AI Infrastructure",
    "export_controls": "Export Controls",
    "capex": "Capital Expenditure",
    "buybacks": "Share Buybacks",
    "rd": "Research and Development",
}


def display_label(key: str, fallback: str) -> str:
    """The label a reader sees for a folded entity.

    Without this the display name depends on ingest order — three filings
    mentioning "Accelerated Computing", "A.I. infrastructure" and "AI
    Infrastructure" would show whichever landed last.
    """
    return _KEY_LABELS.get(key, canonical_label(fallback))


def canonical_label(label: str) -> str:
    """Display form: trimmed and collapsed, original casing preserved."""
    return re.sub(r"\s+", " ", label).strip()


def fold(labels: list[str]) -> dict[str, list[str]]:
    """Group labels by the key they normalise to — the folding, made visible."""
    out: dict[str, list[str]] = {}
    for label in labels:
        out.setdefault(entity_key(label), []).append(label)
    return out
