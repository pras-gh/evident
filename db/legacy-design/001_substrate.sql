-- evident-ingest — structured entities for SEC filings.
--
-- The shape here is driven by one requirement: every retrieved chunk must be
-- able to name the document, the section, the page and the paragraphs it came
-- from. So provenance columns are NOT NULL wherever the parser can guarantee
-- them, and the chunk↔paragraph link is stored explicitly rather than inferred.
--
--   psql "$DATABASE_URL" -f sql/001_schema.sql

-- Preflight: pgvector must be installed on the *server*, not just requested
-- here. Without it every table below still creates and the pipeline still runs
-- end to end — only chunk_embeddings is missing, which fails loudly at write
-- time rather than silently storing nothing.
--   macOS:  brew install pgvector
--   Debian: apt install postgresql-17-pgvector
create extension if not exists vector;

-- ---------------------------------------------------------------- companies
create table if not exists companies (
    cik         text primary key,           -- zero-padded, 10 chars
    name        text        not null,
    ticker      text,
    sic         text,
    updated_at  timestamptz not null default now()
);
create index if not exists companies_ticker_idx on companies (ticker);

-- ---------------------------------------------------------------- documents
create table if not exists documents (
    id             bigserial primary key,
    accession      text        not null unique,   -- 0000320193-25-000073
    cik            text        not null references companies (cik) on delete cascade,
    form_type      text        not null,          -- 10-K, 10-Q, 8-K, DEF 14A
    fiscal_period  text,                          -- FY2025, Q1 2026
    filed_date     date        not null,
    -- when it hit the wire, per EDGAR — never the time we fetched it
    published_at   timestamptz not null,
    source_url     text        not null,
    source_format  text        not null check (source_format in ('html', 'pdf')),
    -- lets a re-ingest detect that the bytes are unchanged and skip the work
    content_sha256 text        not null,
    page_count     int,
    ingested_at    timestamptz not null default now()
);
create index if not exists documents_cik_filed_idx on documents (cik, filed_date desc);
create index if not exists documents_form_idx      on documents (form_type, filed_date desc);

-- ----------------------------------------------------------------- sections
create table if not exists sections (
    id          bigserial primary key,
    document_id bigint not null references documents (id) on delete cascade,
    ordinal     int    not null,
    -- full path, so a citation can read "Part II › Item 7 › Capital Expenditures"
    path        text[] not null,
    title       text   not null,
    level       int    not null,
    start_page  int,
    end_page    int,
    unique (document_id, ordinal)
);
create index if not exists sections_doc_idx on sections (document_id);

-- ------------------------------------------------------------------- blocks
-- One row per paragraph. paragraph_id is deterministic and content-addressed,
-- so re-ingesting an unchanged filing produces identical ids and citations
-- issued earlier keep resolving.
create table if not exists blocks (
    id           bigserial primary key,
    document_id  bigint not null references documents (id) on delete cascade,
    section_id   bigint          references sections  (id) on delete set null,
    paragraph_id text   not null,
    ordinal      int    not null,
    page_number  int,
    text         text   not null,
    char_count   int    not null,
    unique (document_id, paragraph_id)
);
create index if not exists blocks_doc_ordinal_idx on blocks (document_id, ordinal);
create index if not exists blocks_page_idx        on blocks (document_id, page_number);

-- ----------------------------------------------------------- filing_tables
-- Tables are their own entity. Flattening a financial table into prose loses
-- the row/column relationship that makes the numbers mean anything, so cells
-- are kept intact and the table is chunked separately from the surrounding
-- narrative.
create table if not exists filing_tables (
    id          bigserial primary key,
    document_id bigint not null references documents (id) on delete cascade,
    section_id  bigint          references sections  (id) on delete set null,
    table_id    text   not null,
    ordinal     int    not null,
    page_number int,
    caption     text,
    n_rows      int    not null,
    n_cols      int    not null,
    cells       jsonb  not null,          -- [["(in millions)","2025","2024"], ...]
    unique (document_id, table_id)
);
create index if not exists filing_tables_doc_idx on filing_tables (document_id);

-- ------------------------------------------------------------------- chunks
create table if not exists chunks (
    id             bigserial primary key,
    document_id    bigint not null references documents (id) on delete cascade,
    section_id     bigint          references sections  (id) on delete set null,
    chunk_id       text   not null,
    ordinal        int    not null,
    kind           text   not null check (kind in ('prose', 'table')),
    page_start     int,
    page_end       int,
    -- the explicit link back to source paragraphs — this is what lets an
    -- answer cite "paragraph 3" rather than gesturing at a page
    paragraph_ids  text[] not null,
    table_id       text,
    text           text   not null,
    token_estimate int    not null,
    unique (document_id, chunk_id)
);
create index if not exists chunks_doc_idx on chunks (document_id, ordinal);

-- --------------------------------------------------------- chunk_embeddings
-- provider/model/dim travel with the vector so a re-embed, an A/B between
-- providers, or a migration never has to guess what produced a row.
--
-- pgvector needs a fixed dimension per column to build an index. 1536 is the
-- deploy-time default; a provider with a different dimension gets its own
-- table plus index rather than a nullable second column here.
create table if not exists chunk_embeddings (
    chunk_id   bigint      not null references chunks (id) on delete cascade,
    provider   text        not null,
    model      text        not null,
    dim        int         not null check (dim = 1536),
    embedding  vector(1536) not null,
    created_at timestamptz not null default now(),
    primary key (chunk_id, provider, model)
);
create index if not exists chunk_embeddings_ann_idx
    on chunk_embeddings using hnsw (embedding vector_cosine_ops);

-- --------------------------------------------------------------- ingest log
create table if not exists ingest_runs (
    id          bigserial primary key,
    accession   text        not null,
    stage       text        not null,
    status      text        not null check (status in ('ok', 'failed', 'skipped')),
    detail      text,
    started_at  timestamptz not null,
    finished_at timestamptz not null default now()
);
create index if not exists ingest_runs_accession_idx on ingest_runs (accession, finished_at desc);
