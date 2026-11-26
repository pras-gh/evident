"""How complete and how current the tracked companies are.

Read by `python -m workers.sync_worker status` and `GET /v1/sync`, through one
function so the two cannot disagree. Sync only: the API runs it with
`AsyncSession.run_sync`.
"""
from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from .fetch import SyncRun
from .models import Chunk, Company, Document
from .sec import Filing, FinancialFact

ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def _run(r: SyncRun | None) -> dict | None:
    if r is None:
        return None
    return {"started_at": r.started_at, "finished_at": r.finished_at, "seen": r.seen,
            "new": r.new, "fetched": r.fetched, "failed": r.failed, "error": r.error}


def latest_filings(db: Session, *, ciks: list[str] | None = None, company_id: int | None = None,
                   forms: list[str] | None = None, limit: int = 20) -> list[dict]:
    """The newest filings in the index, with whether their text is loaded and
    how long after EDGAR accepted them Parallx knew of them."""
    loaded = select(Document.id).where(Document.accession == Filing.accession).exists()
    stmt = (select(Filing, Company.ticker, Company.name, Company.cik, loaded.label("loaded"))
            .join(Company, Company.id == Filing.company_id)
            .order_by(Filing.accepted_at.desc()).limit(limit))
    if ciks is not None:
        stmt = stmt.where(Company.cik.in_(ciks))
    if company_id is not None:
        stmt = stmt.where(Filing.company_id == company_id)
    if forms:
        stmt = stmt.where(Filing.form_type.in_(forms))
    return [{
        "ticker": ticker, "company": name, "cik": cik, "accession": f.accession,
        "form_type": f.form_type, "filed_date": f.filed_date, "accepted_at": f.accepted_at,
        "report_date": f.report_date, "items": f.items, "description": f.description,
        "primary_document": f.primary_document, "first_seen_at": f.first_seen_at,
        # found as news (by a poll), not as history (by a backfill)
        "alerted": f.alerted_at is not None,
        "minutes_after_filing": (round((f.first_seen_at - f.accepted_at).total_seconds() / 60, 1)
                                 if f.alerted_at is not None else None),
        "loaded": bool(is_loaded),
    } for f, ticker, name, cik, is_loaded in db.execute(stmt).all()]


def status(db: Session, ciks: list[str], *, recent: int = 10) -> dict:
    """Coverage of `ciks` (the tracked companies), the last checks of EDGAR,
    and the newest filings."""
    tracked = Company.cik.in_(ciks)

    def count(*where) -> int:
        return db.execute(select(func.count()).select_from(Company)
                          .where(tracked, *where)).scalar_one()

    k, d = aliased(Chunk), aliased(Document)
    has_doc = exists().where(Document.company_id == Company.id)
    embedded = exists().where(k.document_id == d.id, k.embedding.is_not(None),
                              d.company_id == Company.id)
    unembedded = exists().where(k.document_id == d.id, k.embedding.is_(None),
                                d.company_id == Company.id)
    has_facts = exists().where(FinancialFact.company_id == Company.id)
    last = {kind: db.execute(select(SyncRun).where(SyncRun.kind == kind)
                             .order_by(SyncRun.started_at.desc()).limit(1)).scalar_one_or_none()
            for kind in ("poll", "sweep")}
    return {
        "tracked": len(set(ciks)),
        "indexed": count(Company.filings_indexed_at.is_not(None)),
        "with_financials": count(has_facts),
        "loaded": count(has_doc),
        "searchable": count(embedded, ~unembedded),
        "pending_chunks": db.execute(select(func.count()).select_from(Chunk).where(
            Chunk.embedding.is_(None))).scalar_one(),
        "last_poll": _run(last["poll"]),
        "last_sweep": _run(last["sweep"]),
        "latest_filings": latest_filings(db, ciks=ciks, limit=recent),
    }
