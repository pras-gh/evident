"""The filing store: files that belong to a filing but not in a table.

    {accession}/source/{primary document}     the bytes as fetched
    {accession}/filing.pdf                    a PDF of the filing
    {accession}/pages/{page}.webp             a page as rendered
    {accession}/pages/{page}.thumb.webp       …and its thumbnail

The database stores paths relative to the store's root, so the root can move
(FILING_STORE) without rewriting rows. Every read goes through `resolve`,
which refuses a path that would leave the root: these paths reach an HTTP
handler, and `../../.env` must not be servable.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def root() -> Path:
    return Path(os.environ.get("FILING_STORE") or _REPO / "data" / "filings")


def _clean(part: str) -> str:
    part = _SAFE.sub("_", part).strip("._")
    if not part:
        raise ValueError("empty path part")
    return part


def source_path(accession: str, filename: str) -> str:
    return f"{_clean(accession)}/source/{_clean(filename)}"


def pdf_path(accession: str) -> str:
    return f"{_clean(accession)}/filing.pdf"


def page_path(accession: str, page: int, *, thumbnail: bool = False) -> str:
    return f"{_clean(accession)}/pages/{int(page)}{'.thumb' if thumbnail else ''}.webp"


def resolve(relative: str) -> Path:
    """The absolute path for a stored relative path, or ValueError if it would
    escape the store."""
    base = root().resolve()
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"{relative!r} is outside the filing store")
    return target


def write(relative: str, data: bytes) -> str:
    """Write atomically — a reader never sees half a page image."""
    target = resolve(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as fh:
        fh.write(data)
        tmp = fh.name
    os.replace(tmp, target)
    return relative
