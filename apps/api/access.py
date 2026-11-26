"""Research access: which companies a caller may open.

Every company-scoped endpoint resolves its ticker through `get_company`
here, so none can forget the check. Opening a company needs a row in
`user_company_access` (`POST /v1/research/access` makes one, spending a credit
or a plan's slot); staff need none. Reading what was researched never spends
anything, and a lapsed plan never takes it away.
"""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import Chunk, Company, Document
from parallx_db.accounts import has_access

from . import deps
from .errors import ApiError
from .viewer import Viewer, get_viewer


async def require_access(db: AsyncSession, viewer: Viewer, company_id: int, label: str) -> None:
    if viewer.is_staff:
        return
    if not await db.run_sync(lambda s: has_access(s, viewer.user_id, company_id)):
        raise ApiError(403, "research_required",
                       f"Research {label} to open it: it uses one of your company credits.",
                       ticker=label)


async def get_company(ticker: str, viewer: Viewer = Depends(get_viewer),
                      db: AsyncSession = Depends(deps.get_db)) -> Company:
    """The company, if the caller may research it; 404 if there is none."""
    company = await deps.get_company(ticker, db)
    await require_access(db, viewer, company.id, company.ticker or company.name)
    return company


async def require_document(db: AsyncSession, viewer: Viewer, document_id: int) -> None:
    row = (await db.execute(select(Company.id, Company.ticker, Company.name)
                            .join(Document, Document.company_id == Company.id)
                            .where(Document.id == document_id))).first()
    if row is not None:                    # a missing document is the endpoint's 404
        await require_access(db, viewer, row[0], row[1] or row[2])


async def accessible_chunks(db: AsyncSession, viewer: Viewer, chunk_ids: list[int]) -> set[int]:
    """Of `chunk_ids`, those from companies the caller may research."""
    if viewer.is_staff or not chunk_ids:
        return set(chunk_ids)
    from parallx_db import CompanyAccess
    rows = (await db.execute(
        select(Chunk.id).join(Document, Document.id == Chunk.document_id)
        .join(CompanyAccess, CompanyAccess.company_id == Document.company_id)
        .where(Chunk.id.in_(chunk_ids), CompanyAccess.user_id == viewer.user_id,
               CompanyAccess.status.in_(("pending", "active"))))).scalars().all()
    return set(rows)


async def document_access(document_id: int, viewer: Viewer = Depends(get_viewer),
                          db: AsyncSession = Depends(deps.get_db)) -> None:
    """For the routes keyed by a document: its company must be researched."""
    await require_document(db, viewer, document_id)
