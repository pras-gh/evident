"""Pages as rendered, and where each paragraph sits on them.

A citation is only as good as a reader's ability to see it on the page. Two
things make that possible, and they must describe the *same* page:

* `DocumentPage` — one row per page of a filing: its size, and the image a
  viewer shows (`image_path`, `thumbnail_path`), written by the render worker.
* a **bounding box** per paragraph — `chunks.paragraph_boxes`, keyed by
  paragraph id, in the page's own coordinates.

For a PDF filing the boxes are measured at parse time from the PDF's text
geometry. For an HTML filing — every real 10-K — there is no page geometry
until a browser lays the filing out, so the render worker lays it out in
Chrome on the filing's own page breaks, measures each paragraph there, and
photographs the same pages. The box is measured on the image it is drawn over;
that is the whole guarantee.

Coordinates are points (1/72 in) from the page's top-left, y down, with the
page's size alongside, so a client scales by `rendered_width / width`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, TypedDict

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at


class BBoxJSON(TypedDict):
    """One paragraph's box, as stored in `chunks.paragraph_boxes`."""
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float


class DocumentPage(Base):
    """One page of a filing, as a viewer shows it."""
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    #: the page's size in points
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    #: relative to the filing store (FILING_STORE); null when not rendered
    image_path: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    #: how many paragraphs the page holds, so a viewer can skip empty pages
    paragraph_count: Mapped[int] = mapped_column(Integer, default=0)
    rendered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]
