"""Semantic search over chunks — scoped to the companies a query is about.

Vector search alone ranks every passage on record by meaning, so it answers
"Wesco International" with the nearest NVIDIA paragraph. Before ranking
anything, search works out which companies the query names:

* by rule, the companies Parallx holds filings for (`parallx_retrieval.companies`);
* by model, through the AI gateway, every company it names — including ones
  with no filings here — and the topic left once they are taken out
  (`parallx_ai.query`). When the model is unavailable, rules alone decide.

Then: named companies on record are searched, and only they; a query naming
none searches the request's `ticker` filter, or everything. A named company
with no filings here is never substituted: if EDGAR has it, its latest annual
report is fetched (`fetches.request_fetch`) and the response says so; if not,
the response says that.
"""
from __future__ import annotations

import logging
import time

import anyio
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import Chunk, Company, Document

from ..deps import get_db, get_gateway
from ..errors import ApiError
from ..viewer import Viewer, get_viewer
from ..schemas import (SearchCompanyOut, SearchHitOut, SearchRequest, SearchResponse,
                       SearchScopeOut)

router = APIRouter(tags=["search"])
log = logging.getLogger("parallx.search")


class SearchUnavailable(RuntimeError):
    """Search cannot answer, and the caller should be told why: a 503 with a
    machine-readable `error` and a `message` that says what to do."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error, self.message = error, message


async def _understand(query: str, gw):
    """(intent, "rules+provider:model"), or (None, "rules") when no model could
    read the query. Search must not fail because this step did."""
    from parallx_ai.gateway import AIContext, internal_organization_id
    from parallx_ai.query import understand
    try:
        org = await anyio.to_thread.run_sync(internal_organization_id)
        ctx = AIContext(organization_id=org, source="api:/v1/search")
        intent, r = await anyio.to_thread.run_sync(lambda: understand(query, ctx, gw))
    except Exception as exc:          # the gateway has logged the detail
        log.warning("search: the query was read by rules only (%s: %s)",
                    type(exc).__name__, str(exc)[:200])
        return None, "rules"
    return intent, f"rules+{r.provider}:{r.model}"


async def _fetch_uncovered(db: AsyncSession, mentioned: list[SearchCompanyOut],
                           on_record, grant=None) -> None:
    """For each named company with no filings here: on record after all under
    another ticker (GOOG for GOOGL) — then covered; on EDGAR — then fetched, or
    joined to the fetch under way; otherwise marked for the page to explain.
    `grant(sec)` is asked first, and a company it refuses (no credit left) is
    not fetched."""
    from parallx_parser import edgar
    from .fetches import request_fetch

    todo = [m for m in mentioned if not m.covered]
    if not todo:
        return
    if not edgar.has_user_agent():
        for m in todo:
            m.fetch = "not_configured"
        return
    try:
        await anyio.to_thread.run_sync(edgar.companies)
    except Exception as exc:
        log.warning("search: EDGAR's company list is unavailable (%s)", str(exc)[:200])
        for m in todo:
            m.fetch, m.fetch_error = "failed", "EDGAR could not be reached"
        return
    by_cik = {c.cik: c for c in on_record if c.cik}
    for m in todo:
        sec = edgar.lookup(ticker=m.ticker, name=m.name)
        if sec is None:
            m.fetch = "not_on_edgar"
        elif sec.cik in by_cik:
            c = by_cik[sec.cik]
            m.covered, m.name, m.ticker = True, c.name, c.ticker
        else:
            m.ticker = m.ticker or sec.ticker
            if grant is not None:
                m.access = await grant(sec)
                if m.access == "upgrade_required":
                    continue
            f = await request_fetch(db, cik=sec.cik, ticker=sec.ticker, name=sec.name)
            m.fetch, m.fetch_id, m.fetch_error = f.status, f.id, f.error


def _mentions(query: str, on_record, intent) -> list[SearchCompanyOut]:
    """Companies the query names: those on record by rule, then whatever the
    model found, matched to the record by ticker or name. Each once."""
    from parallx_retrieval.companies import named, same_company, short_name
    out: list[SearchCompanyOut] = []
    seen: set[str] = set()

    def add(name: str, ticker: str | None, covered: bool) -> None:
        key = (ticker or short_name(name)).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(SearchCompanyOut(name=name, ticker=ticker, covered=covered))

    # When a model read the query, it says which companies it names; rules
    # add only tickers written as tickers. Plain words are company names
    # once there are hundreds on record (Target, Block, News Corp).
    for c in named(query, on_record, explicit_only=intent is not None):
        add(c.name, c.ticker, True)
    for nc in (intent.companies if intent else []):
        match = next((c for c in on_record if same_company(nc.name, nc.ticker, c)), None)
        if match:
            add(match.name, match.ticker, True)
        else:
            add(nc.name, nc.ticker.strip().upper().lstrip("$") if nc.ticker else None, False)
    return out


@router.post("/search", response_model=SearchResponse, summary="Semantic search")
async def search(req: SearchRequest, db: AsyncSession = Depends(get_db),
                 gw=Depends(get_gateway), viewer: Viewer = Depends(get_viewer)) -> SearchResponse:
    """Nearest chunks from the companies the query is about, each carrying the
    citation it came from.

    Retrieval is a supporting index, not the product — so a hit that cannot name
    its document, page and paragraph is not a usable result, and the response
    model makes that non-optional.
    """
    from parallx_retrieval.companies import OnRecord
    from parallx_retrieval.embed import EmbeddingError, default_provider
    from parallx_retrieval.rerank import rerank

    started = time.perf_counter()
    # On record = searchable: some of its chunks embedded. A company whose
    # filings are parsed but still queued for embedding (an S&P 500 backfill
    # outrunning its quota) is not, so naming it fetches it — which embeds
    # it now — instead of searching it and finding nothing.
    embedded = (select(Chunk.id).where(Chunk.document_id == Document.id,
                                       Chunk.embedding.is_not(None)).exists())
    on_record = [OnRecord(t, n, cik) for t, n, cik in (await db.execute(
        select(Company.ticker, Company.name, Company.cik)
        .join(Document, Document.company_id == Company.id)
        .where(Company.ticker.is_not(None), embedded).distinct()
        .order_by(Company.ticker))).all()]

    intent, understood_by = await _understand(req.query, gw)
    mentioned = _mentions(req.query, on_record, intent)

    # Researching a company spends a credit the first time (account.start_research);
    # a company the plan has no room for is not searched, nor fetched.
    from .account import start_research
    paywall: dict | None = None

    async def research(company: Company, has_docs: bool) -> str:
        nonlocal paywall
        try:
            r = await start_research(db, viewer, company, has_docs=has_docs)
        except ApiError as exc:
            if exc.error != "upgrade_required":
                raise
            if paywall is None:
                paywall = {"reason": exc.extra.get("reason"), "message": exc.message,
                           "usage": exc.extra.get("usage"), "companies": []}
            paywall["companies"].append(company.name)
            return "upgrade_required"
        return r.outcome

    async def grant(sec) -> str:
        from parallx_db.repositories import upsert_company
        company = await db.run_sync(lambda s_: upsert_company(
            s_, cik=sec.cik, name=sec.name, ticker=sec.ticker))
        return await research(company, False)

    by_ticker = {c.ticker: c for c in (await db.execute(select(Company).where(
        Company.ticker.in_([m.ticker for m in mentioned if m.covered and m.ticker] +
                           ([req.ticker.upper()] if req.ticker else []))))).scalars()}
    for m in mentioned:
        if m.covered and m.ticker in by_ticker:
            m.access = await research(by_ticker[m.ticker], True)
    await _fetch_uncovered(db, mentioned, on_record, grant)
    if mentioned:
        scope = SearchScopeOut(tickers=[m.ticker for m in mentioned if m.covered
                                        and m.access != "upgrade_required"], reason="named")
    elif req.ticker:
        company = by_ticker.get(req.ticker.upper())
        ok = company is not None and await research(company, True) != "upgrade_required"
        scope = SearchScopeOut(tickers=[req.ticker.upper()] if ok else [], reason="filter")
    elif viewer.is_staff:
        scope = SearchScopeOut(tickers=None, reason="all")
    else:
        # naming no company searches the ones this person has researched
        from parallx_db.accounts import researched_companies
        mine = await db.run_sync(lambda s_: researched_companies(s_, viewer.user_id, limit=500))
        scope = SearchScopeOut(tickers=[c.ticker for c, _ in mine if c.ticker], reason="all")
    topic = (intent.topic.strip() or None) if intent else None
    about = dict(query=req.query, companies=mentioned, scope=scope, topic=topic, paywall=paywall,
                 understood_by=understood_by,
                 kind=intent.kind if intent else None,
                 wants_filings=bool(intent and intent.wants_filings),
                 on_record=[SearchCompanyOut(name=c.name, ticker=c.ticker, covered=True)
                            for c in on_record])

    def ms() -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    # Only companies with no filings here: say so, rather than answer with the
    # nearest passage from somebody else's.
    if scope.tickers == []:
        return SearchResponse(hits=[], took_ms=ms(), **about)

    try:
        embedder = default_provider()
    except EmbeddingError as exc:
        raise SearchUnavailable("search_not_configured", str(exc)) from exc
    from parallx_db import EMBEDDING_DIM
    if embedder.dim != EMBEDDING_DIM:
        # a query vector of another width cannot be compared with the column's
        raise SearchUnavailable(
            "search_not_configured",
            f"{embedder.name}/{embedder.model} is configured for {embedder.dim} dimensions; "
            f"chunks.embedding holds {EMBEDDING_DIM}. Unset EMBEDDING_DIM, or migrate the "
            "column and re-embed.")

    # Checked before embedding the query, so an empty index costs no API call
    # and says what to run instead of returning no hits as if none matched.
    indexed = (await db.execute(select(Chunk.id).where(
        Chunk.embedding_provider == embedder.name,
        Chunk.embedding_model == embedder.model).limit(1))).first()
    if indexed is None:
        raise SearchUnavailable(
            "search_not_indexed",
            f"No chunks are embedded with {embedder.name}/{embedder.model} yet. "
            "Run `python workers/embedding_worker.py` to index them.")

    try:
        # the topic, not "what is going on with" — the company is the filter
        vector = embedder.embed_query([topic or req.query])[0]
    except EmbeddingError as exc:
        raise SearchUnavailable("search_unavailable", str(exc)) from exc

    distance = Chunk.embedding.cosine_distance(vector)
    stmt = (select(Chunk, Document, distance.label("distance"))
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.embedding.is_not(None),
                   # Only rows embedded by the provider asking the question.
                   # Cosine distance between two models' vectors is a number
                   # with no meaning — it does not error, it just ranks
                   # wrongly, so a half-re-embedded corpus would answer every
                   # query confidently and incorrectly.
                   Chunk.embedding_provider == embedder.name,
                   Chunk.embedding_model == embedder.model)
            .order_by(distance)
            # over-fetch so re-ranking has room to move things; ordering by
            # distance alone would fix the top k before recency is considered
            .limit(req.k * 3))
    if scope.tickers is not None:
        stmt = stmt.join(Company, Company.id == Document.company_id).where(
            Company.ticker.in_(scope.tickers))
    if req.form_types:
        stmt = stmt.where(Document.form_type.in_(req.form_types))

    rows = (await db.execute(stmt)).all()
    hits = [SearchHitOut(
        chunk_id=c.id, chunk_hash=c.chunk_hash,
        paragraph_ids=list(c.paragraph_ids or []), score=round(1 - float(dist), 6),
        text=c.text, accession=d.accession, form_type=d.form_type,
        filed_at=d.filed_at, page_number=c.page_number,
        section_title=c.section_title, citation=c.citation(),
    ) for c, d, dist in rows]

    # a near-tie goes to the newer filing: a risk disclosed in 2019 and
    # restated in 2025 is mostly interesting in its 2025 form
    hits = rerank(hits)[:req.k]

    return SearchResponse(hits=hits, took_ms=ms(),
                          embedder=f"{embedder.name}/{embedder.model}", **about)
