"""PostgreSQL writer.

Ingestion is idempotent on accession: re-running a filing replaces its derived
rows rather than duplicating them, and the content digest lets an unchanged
filing skip the work entirely. Everything for one document lands in a single
transaction — a half-ingested filing that answers queries with missing pages is
worse than one that is absent.

Requires `psycopg` (v3). Imported lazily so the rest of the package stays
dependency-free.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .embed import EmbeddingBatch
from .models import Chunk, Company, ParsedDocument


def connect(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "Postgres writes need psycopg — `pip install -r requirements.txt`"
        ) from exc
    return psycopg.connect(dsn)


def already_ingested(conn: Any, accession: str, sha256: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select content_sha256 from documents where accession = %s", (accession,)
        )
        row = cur.fetchone()
    return bool(row) and row[0] == sha256


def write_document(
    conn: Any,
    *,
    company: Company,
    parsed: ParsedDocument,
    chunks: list[Chunk],
    embeddings: EmbeddingBatch | None = None,
) -> int:
    """Write one filing atomically. Returns the document id."""
    doc = parsed.document
    if embeddings and len(embeddings.vectors) != len(chunks):
        raise ValueError(
            f"{len(embeddings.vectors)} vectors for {len(chunks)} chunks — refusing "
            "to write embeddings that would be misaligned with their text."
        )

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """insert into companies (cik, name, ticker, sic)
                    values (%s, %s, %s, %s)
               on conflict (cik) do update
                    set name = excluded.name,
                        ticker = coalesce(excluded.ticker, companies.ticker),
                        updated_at = now()""",
            (company.cik, company.name, company.ticker, company.sic),
        )

        # Replacing the row cascades the old sections/blocks/tables/chunks away,
        # so a re-ingest can never leave orphaned paragraphs behind.
        cur.execute("delete from documents where accession = %s", (doc.accession,))
        cur.execute(
            """insert into documents (accession, cik, form_type, fiscal_period,
                                      filed_date, published_at, source_url,
                                      source_format, content_sha256, page_count)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (doc.accession, company.cik, doc.form_type, doc.fiscal_period,
             doc.filed_date, doc.published_at, doc.source_url, doc.source_format,
             doc.content_sha256, doc.page_count),
        )
        document_id = cur.fetchone()[0]

        section_ids: dict[int, int] = {}
        for s in parsed.sections:
            cur.execute(
                """insert into sections (document_id, ordinal, path, title,
                                         level, start_page, end_page)
                        values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                (document_id, s.ordinal, s.path, s.title, s.level,
                 s.start_page, s.end_page),
            )
            section_ids[s.ordinal] = cur.fetchone()[0]

        _copy_blocks(cur, document_id, section_ids, parsed)
        _copy_tables(cur, document_id, section_ids, parsed)
        chunk_ids = _copy_chunks(cur, document_id, section_ids, chunks)

        if embeddings:
            cur.executemany(
                """insert into chunk_embeddings (chunk_id, provider, model, dim, embedding)
                        values (%s,%s,%s,%s,%s)
                   on conflict (chunk_id, provider, model) do update
                        set embedding = excluded.embedding, created_at = now()""",
                [
                    (chunk_ids[c.chunk_id], embeddings.provider, embeddings.model,
                     embeddings.dim, _vector_literal(v))
                    for c, v in zip(chunks, embeddings.vectors)
                ],
            )
    return document_id


def _copy_blocks(cur, document_id, section_ids, parsed) -> None:
    cur.executemany(
        """insert into blocks (document_id, section_id, paragraph_id, ordinal,
                               page_number, text, char_count)
                values (%s,%s,%s,%s,%s,%s,%s)""",
        [
            (document_id, section_ids.get(b.section_ordinal), b.paragraph_id,
             b.ordinal, b.page_number, b.text, b.char_count)
            for b in parsed.blocks
        ],
    )


def _copy_tables(cur, document_id, section_ids, parsed) -> None:
    cur.executemany(
        """insert into filing_tables (document_id, section_id, table_id, ordinal,
                                      page_number, caption, n_rows, n_cols, cells)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        [
            (document_id, section_ids.get(t.section_ordinal), t.table_id, t.ordinal,
             t.page_number, t.caption, t.n_rows, t.n_cols, json.dumps(t.cells))
            for t in parsed.tables
        ],
    )


def _copy_chunks(cur, document_id, section_ids, chunks) -> dict[str, int]:
    ids: dict[str, int] = {}
    for c in chunks:
        cur.execute(
            """insert into chunks (document_id, section_id, chunk_id, ordinal, kind,
                                   page_start, page_end, paragraph_ids, table_id,
                                   text, token_estimate)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (document_id, section_ids.get(c.section_ordinal), c.chunk_id, c.ordinal,
             c.kind, c.page_start, c.page_end, c.paragraph_ids, c.table_id,
             c.text, c.token_estimate),
        )
        ids[c.chunk_id] = cur.fetchone()[0]
    return ids


def _vector_literal(vec: Iterable[float]) -> str:
    """pgvector accepts its own text form: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.7g}" for v in vec) + "]"


def log_run(conn: Any, *, accession: str, stage: str, status: str,
            detail: str | None, started_at) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into ingest_runs (accession, stage, status, detail, started_at)
                    values (%s,%s,%s,%s,%s)""",
            (accession, stage, status, detail, started_at),
        )
    conn.commit()
