-- evident-ingest — layer 3: memory cards.
--
-- Cards are what a person reads; entities are what the system stores. A card is
-- a derived projection over layer 2, never authored by hand, so rebuilding one
-- is deterministic and re-ingesting a filing cannot duplicate it.
--
-- The defining property is that a card is NOT a current value. It is an
-- append-only series of revisions, one per filing that touched it, each
-- carrying a diff against the one before. "CapEx: $14.6B" is a number; "CapEx
-- rose from $10.9B, and the increase is attributed to data-centre expansion"
-- is a card. The second is only possible because the first revision is still
-- there.

-- ------------------------------------------------------------ definitions
create table if not exists card_definitions (
    kind          text primary key,
    title         text   not null,
    description   text,
    -- which layer-2 entities feed this card
    entity_kinds  text[] not null,
    display_order int    not null default 0
);

-- --------------------------------------------------------------- routing
-- Routing rules are data, not code, so a new card ("Segment Margins ← MD&A")
-- is an insert rather than a deploy.
--
-- The six bindings bind at three different granularities, which is why this is
-- a predicate rather than a single column:
--   Revenue     -> form type      (10-Q / 10-K)
--   Products    -> document kind  (earnings call)
--   Guidance    -> speaker role   (CEO statements)
--   Risks       -> section        (Item 1A)
--   CapEx       -> section        (cash flow)
--   Litigation  -> section        (Item 3)
-- A NULL column means "don't care"; a populated one must match.
create table if not exists card_sources (
    id              bigserial primary key,
    card_kind       text   not null references card_definitions (kind) on delete cascade,
    form_types      text[],
    doc_kinds       text[],
    section_pattern text,                  -- case-insensitive regex on section title
    speaker_roles   text[],
    priority        int    not null default 0,
    -- the human-readable half of the user's table, shown as "Updates from"
    source_label    text   not null
);
create index if not exists card_sources_kind_idx on card_sources (card_kind, priority desc);

-- ----------------------------------------------------------------- cards
create table if not exists memory_cards (
    id              bigserial primary key,
    company_id      bigint not null references company_memory  (company_id) on delete cascade,
    kind            text   not null references card_definitions (kind)      on delete cascade,
    revision_count  int    not null default 0,
    first_seen_at   date,
    last_updated_at date,
    unique (company_id, kind)
);

-- ------------------------------------------------------------- revisions
-- Append-only. Nothing here is ever updated in place — an in-place update
-- would destroy the history that is the entire point of the card.
create table if not exists card_revisions (
    id                bigserial primary key,
    card_id           bigint  not null references memory_cards (id) on delete cascade,
    revision          int     not null,
    -- the filing's own date, never the time we ingested it
    as_of             date    not null,
    document_id       bigint  not null references documents (id) on delete cascade,
    source_section_id bigint           references sections  (id) on delete set null,
    source_note       text,                       -- 'FY2025 10-K · Item 7'
    summary           text,
    facts             jsonb   not null,           -- the card body at this revision
    delta             jsonb,                      -- added / removed / changed vs previous
    -- false when a filing touched the card but nothing actually moved; lets the
    -- UI show "6 updates, 2 material" instead of pretending every filing matters
    is_material       boolean not null default false,
    created_at        timestamptz not null default now(),
    unique (card_id, document_id),                -- idempotent per filing
    unique (card_id, revision)
);
create index if not exists card_revisions_card_time_idx
    on card_revisions (card_id, as_of desc);
create index if not exists card_revisions_material_idx
    on card_revisions (card_id, is_material, as_of desc);

create table if not exists card_revision_evidence (
    revision_id bigint not null references card_revisions (id) on delete cascade,
    evidence_id bigint not null references evidence       (id) on delete cascade,
    primary key (revision_id, evidence_id)
);

-- Current state is the newest revision, derived rather than stored, so it can
-- never drift out of sync with the history.
create or replace view card_current as
select distinct on (r.card_id)
       c.company_id, c.kind, r.card_id, r.id as revision_id, r.revision,
       r.as_of, r.summary, r.facts, r.delta, r.is_material, r.source_note
  from card_revisions r
  join memory_cards  c on c.id = r.card_id
 order by r.card_id, r.revision desc;

-- ------------------------------------------------------------------ seed
insert into card_definitions (kind, title, description, entity_kinds, display_order) values
    ('revenue',    'Revenue',    'Reported revenue by period, with restatements flagged.', array['metric'],            1),
    ('products',   'Products',   'Products named by management, and where each one is in its life.', array['product'],  2),
    ('guidance',   'Guidance',   'Forward-looking commitments, carried until something settles them.', array['promise'], 3),
    ('risks',      'Risks',      'Disclosed risk factors, including ones that quietly disappear.', array['risk'],        4),
    ('capex',      'CapEx',      'Capital expenditure, and what management attributes it to.', array['metric'],          5),
    ('litigation', 'Litigation', 'Legal proceedings and their movement between filings.', array['event', 'risk'],        6)
on conflict (kind) do nothing;

insert into card_sources (card_kind, form_types, doc_kinds, section_pattern, speaker_roles, source_label) values
    ('revenue',    array['10-K','10-Q'], null,                    null,                            null,              '10-Q / 10-K'),
    ('products',   null,                 array['earnings_call'],  null,                            null,              'Earnings Call'),
    ('guidance',   null,                 null,                    null,                            array['CEO'],      'CEO statements'),
    ('risks',      null,                 null,                    'item\s*1a|risk\s*factors',      null,              'Risk section'),
    ('capex',      null,                 null,                    'cash\s*flow|capital\s*expend',  null,              'Cash Flow section'),
    ('litigation', null,                 null,                    'item\s*3|legal\s*proceedings',  null,              'Legal section')
on conflict do nothing;
