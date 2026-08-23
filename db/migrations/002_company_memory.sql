-- elevate-ingest — layer 2: company memory.
--
-- 001 stores *what was filed*: documents, sections, paragraphs, tables. That is
-- substrate. This file stores *what we know*: a durable, typed model of the
-- company that accumulates across every filing it has ever made.
--
-- The distinction matters because the two answer different questions. Layer 1
-- answers "what does page 87 say". Layer 2 answers "when did they first mention
-- Blackwell", "who has run this segment", "what did they promise in 2024 and did
-- they deliver". No amount of similarity search over layer 1 produces those —
-- they need resolved entities with a time axis.
--
-- Every row here is an assertion, and every assertion points at evidence. That
-- is the invariant the whole product rests on: nothing enters memory without a
-- paragraph that says it.

-- ------------------------------------------------------------------- memory
create table if not exists company_memory (
    company_id    bigserial primary key,
    cik           text        not null unique references companies (cik) on delete cascade,
    ticker        text,
    built_at      timestamptz,
    document_count int       not null default 0
);

-- ----------------------------------------------------------------- evidence
-- The join between substrate and memory. An entity without evidence is a
-- rumour, so every typed table below references this one.
create table if not exists evidence (
    id           bigserial primary key,
    document_id  bigint not null references documents (id) on delete cascade,
    block_id     bigint          references blocks (id) on delete cascade,
    table_id     bigint          references filing_tables (id) on delete cascade,
    paragraph_id text,
    page_number  int,
    quote        text   not null,         -- the exact span that supports the claim
    char_start   int,
    char_end     int,
    -- exactly one anchor: a paragraph or a table, never neither
    constraint evidence_has_anchor check (block_id is not null or table_id is not null)
);
create index if not exists evidence_document_idx on evidence (document_id);

-- ------------------------------------------------------------------- topics
create table if not exists topics (
    id            bigserial primary key,
    company_id    bigint not null references company_memory (company_id) on delete cascade,
    slug          text   not null,
    label         text   not null,
    first_seen_at date,
    last_seen_at  date,
    mention_count int    not null default 0,
    unique (company_id, slug)
);
create table if not exists topic_mentions (
    topic_id    bigint not null references topics   (id) on delete cascade,
    evidence_id bigint not null references evidence (id) on delete cascade,
    observed_at date   not null,
    primary key (topic_id, evidence_id)
);

-- ------------------------------------------------------------------- people
create table if not exists people (
    id            bigserial primary key,
    company_id    bigint not null references company_memory (company_id) on delete cascade,
    full_name     text   not null,
    normalised    text   not null,
    first_seen_at date,
    last_seen_at  date,
    unique (company_id, normalised)
);
-- Roles are dated because executives change jobs, and "the CFO said" means a
-- different person depending on the year.
create table if not exists person_roles (
    id          bigserial primary key,
    person_id   bigint not null references people   (id) on delete cascade,
    role        text   not null,
    from_date   date,
    to_date     date,
    evidence_id bigint          references evidence (id) on delete set null
);
create table if not exists person_statements (
    id          bigserial primary key,
    person_id   bigint not null references people   (id) on delete cascade,
    evidence_id bigint not null references evidence (id) on delete cascade,
    said_at     date   not null,
    topic_id    bigint          references topics   (id) on delete set null
);

-- ------------------------------------------------------------------ metrics
create table if not exists metrics (
    id         bigserial primary key,
    company_id bigint not null references company_memory (company_id) on delete cascade,
    name       text   not null,
    normalised text   not null,
    unit       text,
    unique (company_id, normalised)
);
-- One row per (metric, period). This is what makes a metric a series rather
-- than a number someone mentioned once.
create table if not exists metric_observations (
    id          bigserial primary key,
    metric_id   bigint  not null references metrics  (id) on delete cascade,
    document_id bigint  not null references documents (id) on delete cascade,
    evidence_id bigint  not null references evidence (id) on delete cascade,
    period      text    not null,          -- FY2025, Q1 2026
    period_end  date,
    value       numeric,
    unit        text,
    is_restated boolean not null default false,
    unique (metric_id, period, document_id)
);

-- -------------------------------------------------------------------- risks
create table if not exists risks (
    id            bigserial primary key,
    company_id    bigint not null references company_memory (company_id) on delete cascade,
    slug          text   not null,
    label         text   not null,
    category      text,
    first_seen_at date,
    last_seen_at  date,
    -- a risk that stops being disclosed is a signal, so we track disappearance
    status        text   not null default 'active'
                  check (status in ('active', 'dropped')),
    unique (company_id, slug)
);
create table if not exists risk_observations (
    id          bigserial primary key,
    risk_id     bigint not null references risks    (id) on delete cascade,
    document_id bigint not null references documents (id) on delete cascade,
    evidence_id bigint not null references evidence (id) on delete cascade,
    observed_at date   not null,
    severity    text                                -- language, not a score
);

-- ----------------------------------------------------------------- promises
-- The entity a vector database cannot represent. A forward-looking statement is
-- not a fact — it is an open obligation with a lifecycle, resolved by a later
-- filing that either delivers or quietly does not.
create table if not exists promises (
    id                  bigserial primary key,
    company_id          bigint not null references company_memory (company_id) on delete cascade,
    statement           text   not null,
    made_at             date   not null,
    made_evidence_id    bigint not null references evidence (id) on delete cascade,
    horizon             text,                       -- "H2 2025", "next fiscal year"
    due_date            date,
    topic_id            bigint          references topics (id) on delete set null,
    status              text   not null default 'open'
                        check (status in ('open', 'kept', 'broken', 'abandoned', 'unclear')),
    resolved_at         date,
    resolved_evidence_id bigint         references evidence (id) on delete set null,
    resolution_note     text,
    -- a resolved promise must say what resolved it
    constraint promise_resolution_has_evidence
        check (status = 'open' or resolved_evidence_id is not null or status = 'abandoned')
);
create index if not exists promises_open_idx on promises (company_id, status, due_date);

-- ----------------------------------------------------------------- products
create table if not exists products (
    id            bigserial primary key,
    company_id    bigint not null references company_memory (company_id) on delete cascade,
    name          text   not null,
    normalised    text   not null,
    first_seen_at date,
    last_seen_at  date,
    status        text   not null default 'mentioned'
                  check (status in ('mentioned', 'announced', 'shipping', 'discontinued')),
    unique (company_id, normalised)
);
create table if not exists product_mentions (
    product_id  bigint not null references products (id) on delete cascade,
    evidence_id bigint not null references evidence (id) on delete cascade,
    observed_at date   not null,
    primary key (product_id, evidence_id)
);

-- ------------------------------------------------------------------- events
create table if not exists events (
    id          bigserial primary key,
    company_id  bigint not null references company_memory (company_id) on delete cascade,
    kind        text   not null,           -- guidance_change, departure, acquisition, launch
    headline    text   not null,
    occurred_at date   not null,
    document_id bigint not null references documents (id) on delete cascade,
    evidence_id bigint not null references evidence (id) on delete cascade
);

-- ----------------------------------------------------------------- timeline
-- A materialised spine over everything above, so "show me this company's story"
-- is one indexed read instead of a nine-way union at query time.
create table if not exists timeline (
    id          bigserial primary key,
    company_id  bigint not null references company_memory (company_id) on delete cascade,
    occurred_at date   not null,
    kind        text   not null,           -- topic|person|metric|risk|promise|product|event|filing
    ref_table   text   not null,
    ref_id      bigint not null,
    headline    text   not null,
    topic_id    bigint          references topics   (id) on delete set null,
    evidence_id bigint          references evidence (id) on delete set null,
    unique (company_id, kind, ref_table, ref_id, occurred_at)
);
create index if not exists timeline_company_time_idx on timeline (company_id, occurred_at desc);
create index if not exists timeline_topic_idx        on timeline (topic_id, occurred_at desc);
