"""How complete and how current the tracked companies are.

Read by `python -m workers.sync_worker status` and `GET /v1/sync`, through one
function so the two cannot disagree. Sync only: the API runs it with
`AsyncSession.run_sync`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session, aliased

from .fetch import CompanyFetch, SyncRun
from .models import Chunk, Company, Document

ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def _run(r: SyncRun | None) -> dict | None:
    if r is None:
        return None
    return {"started_at": r.started_at, "finished_at": r.finished_at, "seen": r.seen,
            "new": r.new, "fetched": r.fetched, "failed": r.failed, "error": r.error}


def status(db: Session, ciks: list[str], *, recent: int = 10) -> dict:
    """Coverage of `ciks` (the tracked companies), the last checks of EDGAR,
    and the newest filings fetched as they came in, with how long after EDGAR
    accepted each one it was on record."""
    tracked = select(Company.id).where(Company.cik.in_(ciks))
    with_annual = db.execute(
        select(func.count(func.distinct(Document.company_id)))
        .where(Document.company_id.in_(tracked), Document.form_type.in_(ANNUAL_FORMS))
    ).scalar_one()
    k, d = aliased(Chunk), aliased(Document)
    # searchable: every chunk of every filing embedded
    unembedded = exists().where(k.document_id == d.id, k.embedding.is_(None),
                                d.company_id == Company.id)
    embedded = exists().where(k.document_id == d.id, k.embedding.is_not(None),
                              d.company_id == Company.id)
    searchable = db.execute(select(func.count()).select_from(Company).where(
        Company.cik.in_(ciks), embedded, ~unembedded)).scalar_one()
    pending = db.execute(select(func.count()).select_from(Chunk).where(
        Chunk.embedding.is_(None))).scalar_one()

    last = {kind: db.execute(select(SyncRun).where(SyncRun.kind == kind)
                             .order_by(SyncRun.started_at.desc()).limit(1)).scalar_one_or_none()
            for kind in ("poll", "sweep")}

    since = datetime.now(timezone.utc) - timedelta(days=1)
    failed = db.execute(select(func.count()).select_from(CompanyFetch).where(
        CompanyFetch.source == "backfill", CompanyFetch.status == "failed",
        CompanyFetch.finished_at > since)).scalar_one()

    rows = db.execute(
        select(CompanyFetch, Document.published_at)
        .join(Document, and_(Document.accession == CompanyFetch.accession), isouter=True)
        .where(CompanyFetch.source == "watch", CompanyFetch.status == "done")
        .order_by(CompanyFetch.finished_at.desc()).limit(recent)).all()
    latest = [{
        "ticker": f.ticker, "name": f.name, "form_type": f.form_type,
        "accession": f.accession, "accepted_at": accepted, "fetched_at": f.finished_at,
        "minutes_after_filing": (round((f.finished_at - accepted).total_seconds() / 60, 1)
                                 if accepted and f.finished_at else None),
    } for f, accepted in rows]

    return {
        "tracked": len(set(ciks)),
        "with_annual_report": with_annual,
        "searchable": searchable,
        "pending_chunks": pending,
        "backfill_failures_last_day": failed,
        "last_poll": _run(last["poll"]),
        "last_sweep": _run(last["sweep"]),
        "latest_filings": latest,
    }
