-- parallx — full schema, generated from the Alembic chain.
--
-- DO NOT EDIT. Regenerate with:
--     python3 tools/build_schema.py
--
-- Migrations are the source of truth and live in db/alembic/versions/. Apply
-- this file to a brand-new database, or run `alembic upgrade head` against
-- anything that already exists.

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);


CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE companies (
    id BIGSERIAL NOT NULL, 
    cik VARCHAR(10) NOT NULL, 
    ticker VARCHAR(16), 
    name VARCHAR(255) NOT NULL, 
    sic VARCHAR(16), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_companies PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_companies_cik ON companies (cik);

CREATE INDEX ix_companies_ticker ON companies (ticker);

CREATE TABLE documents (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    accession VARCHAR(32) NOT NULL, 
    form_type VARCHAR(16) NOT NULL, 
    fiscal_period VARCHAR(16), 
    filed_at DATE NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    source_url TEXT NOT NULL, 
    source_format VARCHAR(16) NOT NULL, 
    content_sha256 VARCHAR(64) NOT NULL, 
    page_count INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_documents PRIMARY KEY (id), 
    CONSTRAINT ck_documents_source_format CHECK (source_format in ('html','pdf')), 
    CONSTRAINT fk_documents_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_documents_accession ON documents (accession);

CREATE INDEX ix_documents_company_id ON documents (company_id);

CREATE INDEX ix_documents_company_id_filed_at ON documents (company_id, filed_at);

CREATE INDEX ix_documents_form_type ON documents (form_type);

CREATE TABLE metrics (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    normalised VARCHAR(255) NOT NULL, 
    unit VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_metrics PRIMARY KEY (id), 
    CONSTRAINT fk_metrics_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_metrics_company_id_normalised UNIQUE (company_id, normalised)
);

CREATE INDEX ix_metrics_company_id ON metrics (company_id);

CREATE TABLE topics (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    slug VARCHAR(255) NOT NULL, 
    label VARCHAR(255) NOT NULL, 
    first_seen_at DATE, 
    last_seen_at DATE, 
    mention_count INTEGER DEFAULT '0' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_topics PRIMARY KEY (id), 
    CONSTRAINT fk_topics_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_topics_company_id_slug UNIQUE (company_id, slug)
);

CREATE INDEX ix_topics_company_id ON topics (company_id);

CREATE TABLE chunks (
    id BIGSERIAL NOT NULL, 
    document_id BIGINT NOT NULL, 
    paragraph_id VARCHAR(64) NOT NULL, 
    ordinal INTEGER NOT NULL, 
    page_number INTEGER, 
    section_title VARCHAR(255), 
    section_path TEXT[], 
    text TEXT NOT NULL, 
    char_count INTEGER NOT NULL, 
    token_estimate INTEGER NOT NULL, 
    embedding VECTOR(1536), 
    embedding_provider VARCHAR(64), 
    embedding_model VARCHAR(64), 
    embedded_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_chunks PRIMARY KEY (id), 
    CONSTRAINT fk_chunks_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT uq_chunks_document_id_paragraph_id UNIQUE (document_id, paragraph_id)
);

CREATE INDEX ix_chunks_document_id ON chunks (document_id);

CREATE INDEX ix_chunks_document_id_ordinal ON chunks (document_id, ordinal);

CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE metric_observations (
    id BIGSERIAL NOT NULL, 
    metric_id BIGINT NOT NULL, 
    document_id BIGINT NOT NULL, 
    chunk_id BIGINT, 
    period VARCHAR(64) NOT NULL, 
    period_end DATE, 
    value NUMERIC(20, 4), 
    unit VARCHAR(64), 
    is_restated BOOLEAN DEFAULT 'false' NOT NULL, 
    CONSTRAINT pk_metric_observations PRIMARY KEY (id), 
    CONSTRAINT fk_metric_observations_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL, 
    CONSTRAINT fk_metric_observations_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_metric_observations_metric_id_metrics FOREIGN KEY(metric_id) REFERENCES metrics (id) ON DELETE CASCADE, 
    CONSTRAINT uq_metric_observations_metric_id_period_document_id UNIQUE (metric_id, period, document_id)
);

CREATE INDEX ix_metric_observations_metric_id ON metric_observations (metric_id);

CREATE TABLE people (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    chunk_id BIGINT, 
    full_name VARCHAR(255) NOT NULL, 
    normalised VARCHAR(255) NOT NULL, 
    roles JSONB, 
    first_seen_at DATE, 
    last_seen_at DATE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_people PRIMARY KEY (id), 
    CONSTRAINT fk_people_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL, 
    CONSTRAINT fk_people_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_people_company_id_normalised UNIQUE (company_id, normalised)
);

CREATE INDEX ix_people_company_id ON people (company_id);

CREATE INDEX ix_people_normalised ON people (normalised);

CREATE TABLE risks (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    chunk_id BIGINT, 
    slug VARCHAR(255) NOT NULL, 
    label TEXT NOT NULL, 
    category VARCHAR(64), 
    severity VARCHAR(64), 
    status VARCHAR(16) DEFAULT 'active' NOT NULL, 
    first_seen_at DATE, 
    last_seen_at DATE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_risks PRIMARY KEY (id), 
    CONSTRAINT ck_risks_status CHECK (status in ('active','dropped')), 
    CONSTRAINT fk_risks_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL, 
    CONSTRAINT fk_risks_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_risks_company_id_slug UNIQUE (company_id, slug)
);

CREATE INDEX ix_risks_company_id ON risks (company_id);

CREATE TABLE timeline_events (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    document_id BIGINT, 
    chunk_id BIGINT, 
    topic_id BIGINT, 
    kind VARCHAR(64) NOT NULL, 
    headline TEXT NOT NULL, 
    detail TEXT, 
    occurred_at DATE NOT NULL, 
    ref VARCHAR(255) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_timeline_events PRIMARY KEY (id), 
    CONSTRAINT fk_timeline_events_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL, 
    CONSTRAINT fk_timeline_events_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_timeline_events_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_timeline_events_topic_id_topics FOREIGN KEY(topic_id) REFERENCES topics (id) ON DELETE SET NULL, 
    CONSTRAINT uq_timeline_events_company_id_kind_ref_occurred_at UNIQUE (company_id, kind, ref, occurred_at)
);

CREATE INDEX ix_timeline_events_company_id ON timeline_events (company_id);

CREATE INDEX ix_timeline_events_company_id_occurred_at ON timeline_events (company_id, occurred_at);

CREATE INDEX ix_timeline_events_kind ON timeline_events (kind);

CREATE INDEX ix_timeline_events_topic_id ON timeline_events (topic_id);

CREATE TABLE topic_mentions (
    id BIGSERIAL NOT NULL, 
    topic_id BIGINT NOT NULL, 
    chunk_id BIGINT NOT NULL, 
    document_id BIGINT NOT NULL, 
    observed_at DATE NOT NULL, 
    quote TEXT NOT NULL, 
    CONSTRAINT pk_topic_mentions PRIMARY KEY (id), 
    CONSTRAINT fk_topic_mentions_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_topic_mentions_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_topic_mentions_topic_id_topics FOREIGN KEY(topic_id) REFERENCES topics (id) ON DELETE CASCADE, 
    CONSTRAINT uq_topic_mentions_topic_id_chunk_id UNIQUE (topic_id, chunk_id)
);

CREATE INDEX ix_topic_mentions_chunk_id ON topic_mentions (chunk_id);

CREATE INDEX ix_topic_mentions_topic_id ON topic_mentions (topic_id);

CREATE INDEX ix_topic_mentions_topic_id_observed_at ON topic_mentions (topic_id, observed_at);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;


ALTER TABLE chunks RENAME paragraph_id TO chunk_key;

ALTER INDEX IF EXISTS uq_chunks_document_id_paragraph_id RENAME TO uq_chunks_document_id_chunk_key;

ALTER TABLE chunks ADD COLUMN paragraph_ids TEXT[] DEFAULT '{}' NOT NULL;

UPDATE chunks SET paragraph_ids = ARRAY[chunk_key] WHERE paragraph_ids = '{}';

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';


ALTER TABLE chunks ALTER COLUMN chunk_key TYPE VARCHAR(64);

ALTER TABLE chunks RENAME chunk_key TO chunk_hash;

ALTER TABLE chunks DROP CONSTRAINT IF EXISTS uq_chunks_document_id_chunk_key;

DELETE FROM chunks a USING chunks b WHERE a.id > b.id AND a.chunk_hash = b.chunk_hash;

ALTER TABLE chunks ADD CONSTRAINT uq_chunks_chunk_hash UNIQUE (chunk_hash);

ALTER TABLE topic_mentions ADD COLUMN page_number INTEGER;

ALTER TABLE topic_mentions ADD COLUMN paragraph_id VARCHAR(64);

ALTER TABLE topic_mentions ADD COLUMN confidence FLOAT;

CREATE INDEX ix_topic_mentions_paragraph_id ON topic_mentions (paragraph_id);

ALTER TABLE risks ADD COLUMN page_number INTEGER;

ALTER TABLE risks ADD COLUMN paragraph_id VARCHAR(64);

ALTER TABLE risks ADD COLUMN confidence FLOAT;

CREATE INDEX ix_risks_paragraph_id ON risks (paragraph_id);

ALTER TABLE people ADD COLUMN page_number INTEGER;

ALTER TABLE people ADD COLUMN paragraph_id VARCHAR(64);

ALTER TABLE people ADD COLUMN confidence FLOAT;

CREATE INDEX ix_people_paragraph_id ON people (paragraph_id);

ALTER TABLE metric_observations ADD COLUMN page_number INTEGER;

ALTER TABLE metric_observations ADD COLUMN paragraph_id VARCHAR(64);

ALTER TABLE metric_observations ADD COLUMN confidence FLOAT;

CREATE INDEX ix_metric_observations_paragraph_id ON metric_observations (paragraph_id);

ALTER TABLE timeline_events ADD COLUMN page_number INTEGER;

ALTER TABLE timeline_events ADD COLUMN paragraph_id VARCHAR(64);

ALTER TABLE timeline_events ADD COLUMN confidence FLOAT;

CREATE INDEX ix_timeline_events_paragraph_id ON timeline_events (paragraph_id);

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';


CREATE TABLE entities (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    key VARCHAR(255) NOT NULL, 
    label VARCHAR(255) NOT NULL, 
    attributes JSONB DEFAULT '{}' NOT NULL, 
    status VARCHAR(16) DEFAULT 'active' NOT NULL, 
    first_seen_at DATE, 
    last_seen_at DATE, 
    mention_count INTEGER DEFAULT '0' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_entities PRIMARY KEY (id), 
    CONSTRAINT fk_entities_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_entities_company_id_kind_key UNIQUE (company_id, kind, key), 
    CONSTRAINT ck_entities_ck_entities_kind CHECK (kind in ('topic','strategy','person','product','metric','risk','event','segment')), 
    CONSTRAINT ck_entities_ck_entities_status CHECK (status in ('active','dropped','superseded'))
);

CREATE INDEX ix_entities_company_id ON entities (company_id);

CREATE INDEX ix_entities_kind ON entities (kind);

CREATE INDEX ix_entities_company_id_kind ON entities (company_id, kind);

CREATE TABLE entity_mentions (
    id BIGSERIAL NOT NULL, 
    entity_id BIGINT NOT NULL, 
    document_id BIGINT NOT NULL, 
    chunk_id BIGINT, 
    observed_at DATE NOT NULL, 
    quote TEXT NOT NULL, 
    page_number INTEGER, 
    paragraph_id VARCHAR(64), 
    chunk_hash VARCHAR(64), 
    confidence FLOAT, 
    CONSTRAINT pk_entity_mentions PRIMARY KEY (id), 
    CONSTRAINT fk_entity_mentions_entity_id_entities FOREIGN KEY(entity_id) REFERENCES entities (id) ON DELETE CASCADE, 
    CONSTRAINT fk_entity_mentions_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_entity_mentions_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE CASCADE, 
    CONSTRAINT uq_entity_mentions_entity_id_chunk_id UNIQUE (entity_id, chunk_id)
);

CREATE INDEX ix_entity_mentions_entity_id ON entity_mentions (entity_id);

CREATE INDEX ix_entity_mentions_document_id ON entity_mentions (document_id);

CREATE INDEX ix_entity_mentions_chunk_id ON entity_mentions (chunk_id);

CREATE INDEX ix_entity_mentions_paragraph_id ON entity_mentions (paragraph_id);

CREATE INDEX ix_entity_mentions_entity_id_observed_at ON entity_mentions (entity_id, observed_at);

CREATE TABLE relationships (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    source_entity_id BIGINT NOT NULL, 
    target_entity_id BIGINT NOT NULL, 
    kind VARCHAR(64) NOT NULL, 
    weight INTEGER DEFAULT '1' NOT NULL, 
    document_ids BIGINT[] DEFAULT '{}' NOT NULL, 
    attributes JSONB DEFAULT '{}' NOT NULL, 
    first_seen_at DATE, 
    last_seen_at DATE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_relationships PRIMARY KEY (id), 
    CONSTRAINT fk_relationships_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_relationships_source_entity_id_entities FOREIGN KEY(source_entity_id) REFERENCES entities (id) ON DELETE CASCADE, 
    CONSTRAINT fk_relationships_target_entity_id_entities FOREIGN KEY(target_entity_id) REFERENCES entities (id) ON DELETE CASCADE, 
    CONSTRAINT uq_relationships_source_target_kind UNIQUE (source_entity_id, target_entity_id, kind), 
    CONSTRAINT ck_relationships_ck_relationships_no_self_edge CHECK (source_entity_id <> target_entity_id)
);

CREATE INDEX ix_relationships_company_id ON relationships (company_id);

CREATE INDEX ix_relationships_company_id_kind ON relationships (company_id, kind);

CREATE INDEX ix_relationships_source_entity_id ON relationships (source_entity_id);

CREATE INDEX ix_relationships_target_entity_id ON relationships (target_entity_id);

INSERT INTO entities (company_id, kind, key, label, attributes, status,
                              first_seen_at, last_seen_at, mention_count)
        SELECT company_id, 'topic', slug, label, '{}'::jsonb, 'active',
               first_seen_at, last_seen_at, mention_count
          FROM topics
        ON CONFLICT DO NOTHING;

INSERT INTO entities (company_id, kind, key, label, attributes, status,
                              first_seen_at, last_seen_at)
        SELECT company_id, 'person', normalised, full_name,
               jsonb_strip_nulls(jsonb_build_object('roles', roles)),
               'active', first_seen_at, last_seen_at
          FROM people
        ON CONFLICT DO NOTHING;

INSERT INTO entities (company_id, kind, key, label, attributes, status,
                              first_seen_at, last_seen_at)
        SELECT company_id, 'risk', slug, label,
               jsonb_strip_nulls(jsonb_build_object('category', category,
                                                    'severity', severity)),
               status, first_seen_at, last_seen_at
          FROM risks
        ON CONFLICT DO NOTHING;

INSERT INTO entities (company_id, kind, key, label, attributes, status)
        SELECT company_id, 'metric', normalised, name,
               jsonb_strip_nulls(jsonb_build_object('unit', unit)), 'active'
          FROM metrics
        ON CONFLICT DO NOTHING;

INSERT INTO entity_mentions (entity_id, document_id, chunk_id, observed_at,
                                     quote, page_number, paragraph_id, confidence)
        SELECT e.id, tm.document_id, tm.chunk_id, tm.observed_at, tm.quote,
               tm.page_number, tm.paragraph_id, tm.confidence
          FROM topic_mentions tm
          JOIN topics t ON t.id = tm.topic_id
          JOIN entities e ON e.company_id = t.company_id
                         AND e.kind = 'topic' AND e.key = t.slug
        ON CONFLICT DO NOTHING;

ALTER TABLE metric_observations ADD COLUMN entity_id BIGINT;

UPDATE metric_observations mo
           SET entity_id = e.id
          FROM metrics m
          JOIN entities e ON e.company_id = m.company_id
                         AND e.kind = 'metric' AND e.key = m.normalised
         WHERE mo.metric_id = m.id;

DELETE FROM metric_observations WHERE entity_id IS NULL;

ALTER TABLE metric_observations ALTER COLUMN entity_id SET NOT NULL;

ALTER TABLE metric_observations DROP CONSTRAINT uq_metric_observations_metric_id_period_document_id;

ALTER TABLE metric_observations DROP COLUMN metric_id;

ALTER TABLE metric_observations ADD CONSTRAINT fk_metric_observations_entity_id_entities FOREIGN KEY(entity_id) REFERENCES entities (id) ON DELETE CASCADE;

ALTER TABLE metric_observations ADD CONSTRAINT uq_metric_observations_entity_id_period_document_id UNIQUE (entity_id, period, document_id);

CREATE INDEX ix_metric_observations_entity_id ON metric_observations (entity_id);

ALTER TABLE timeline_events ADD COLUMN entity_id BIGINT;

UPDATE timeline_events te
           SET entity_id = e.id
          FROM topics t
          JOIN entities e ON e.company_id = t.company_id
                         AND e.kind = 'topic' AND e.key = t.slug
         WHERE te.topic_id = t.id;

DROP INDEX ix_timeline_events_topic_id;

ALTER TABLE timeline_events DROP COLUMN topic_id;

ALTER TABLE timeline_events ADD CONSTRAINT fk_timeline_events_entity_id_entities FOREIGN KEY(entity_id) REFERENCES entities (id) ON DELETE SET NULL;

CREATE INDEX ix_timeline_events_entity_id ON timeline_events (entity_id);

DROP TABLE topic_mentions;

DROP TABLE topics;

DROP TABLE people;

DROP TABLE risks;

DROP TABLE metrics;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';


ALTER TABLE entities DROP CONSTRAINT uq_entities_company_id_kind_key;

ALTER TABLE entities DROP CONSTRAINT ck_entities_ck_entities_kind;

DROP INDEX ix_entities_company_id_kind;

ALTER TABLE entities RENAME kind TO entity_type;

ALTER TABLE entities RENAME key TO slug;

ALTER TABLE entities RENAME label TO name;

ALTER TABLE entities RENAME first_seen_at TO first_seen;

ALTER TABLE entities RENAME last_seen_at TO latest_seen;

alter index ix_entities_kind rename to ix_entities_entity_type;

ALTER TABLE entities ADD COLUMN description TEXT;

ALTER TABLE entities ADD COLUMN importance_score FLOAT DEFAULT '0' NOT NULL;

update entities set entity_type = 'strategy' where entity_type = 'topic';

update entities set entity_type = 'executive' where entity_type = 'person';

create temporary table _entity_merge on commit drop as
        select id as dup_id,
               first_value(id) over (partition by company_id, slug
                                     order by id) as keep_id
        from entities;

delete from entity_mentions m
        using _entity_merge x, entity_mentions k
        where m.entity_id = x.dup_id
          and x.dup_id <> x.keep_id
          and k.entity_id = x.keep_id
          and k.chunk_id is not distinct from m.chunk_id;

update entity_mentions m set entity_id = x.keep_id
        from _entity_merge x
        where m.entity_id = x.dup_id and x.dup_id <> x.keep_id;

update metric_observations o set entity_id = x.keep_id
        from _entity_merge x
        where o.entity_id = x.dup_id and x.dup_id <> x.keep_id;

update entities e
        set mention_count = agg.mention_count,
            first_seen    = agg.first_seen,
            latest_seen   = agg.latest_seen,
            attributes    = agg.attributes
        from (
            select x.keep_id,
                   sum(d.mention_count)                       as mention_count,
                   min(d.first_seen)                          as first_seen,
                   max(d.latest_seen)                         as latest_seen,
                   jsonb_object_agg(k, v)
                       filter (where k is not null)           as attributes
            from _entity_merge x
            join entities d on d.id = x.dup_id
            left join lateral jsonb_each(d.attributes) as a(k, v) on true
            group by x.keep_id
        ) agg
        where e.id = agg.keep_id;

delete from entities e
        using _entity_merge x
        where e.id = x.dup_id and x.dup_id <> x.keep_id;

ALTER TABLE entities ADD CONSTRAINT uq_entities_company_id_slug UNIQUE (company_id, slug);

ALTER TABLE entities ADD CONSTRAINT ck_entities_entity_type CHECK (entity_type in ('strategy','product','executive','risk','metric','segment','company','geography'));

ALTER TABLE entities ADD CONSTRAINT ck_entities_importance_score CHECK (importance_score between 0 and 100);

CREATE INDEX ix_entities_company_id_entity_type ON entities (company_id, entity_type);

ALTER TABLE entity_mentions RENAME page_number TO page;

delete from relationships;

ALTER TABLE relationships DROP CONSTRAINT uq_relationships_source_target_kind;

DROP INDEX ix_relationships_company_id_kind;

ALTER TABLE relationships RENAME kind TO relationship_type;

ALTER TABLE relationships RENAME first_seen_at TO first_seen;

ALTER TABLE relationships RENAME last_seen_at TO latest_seen;

ALTER TABLE relationships DROP COLUMN weight;

ALTER TABLE relationships ADD COLUMN strength FLOAT DEFAULT '0' NOT NULL;

ALTER TABLE relationships ADD COLUMN evidence_chunk_id BIGINT;

ALTER TABLE relationships ADD CONSTRAINT fk_relationships_evidence_chunk_id_chunks FOREIGN KEY(evidence_chunk_id) REFERENCES chunks (id) ON DELETE SET NULL;

CREATE INDEX ix_relationships_evidence_chunk_id ON relationships (evidence_chunk_id);

ALTER TABLE relationships ADD CONSTRAINT uq_relationships_source_target_type UNIQUE (source_entity_id, target_entity_id, relationship_type);

ALTER TABLE relationships ADD CONSTRAINT ck_relationships_strength CHECK (strength between 0 and 1);

CREATE INDEX ix_relationships_company_id_relationship_type ON relationships (company_id, relationship_type);

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';


DROP INDEX ix_chunks_embedding;

update chunks set embedding = null, embedding_provider = null, embedding_model = null where embedding is not null;

ALTER TABLE chunks ALTER COLUMN embedding TYPE VECTOR(1024) USING null;

CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

UPDATE alembic_version SET version_num='0006' WHERE alembic_version.version_num = '0005';


CREATE TABLE extraction_calls (
    id BIGSERIAL NOT NULL, 
    run_id UUID NOT NULL, 
    company_id BIGINT NOT NULL, 
    document_id BIGINT NOT NULL, 
    chunk_id BIGINT, 
    prompt_id VARCHAR(64) NOT NULL, 
    model VARCHAR(64) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    rejection_reason TEXT, 
    stop_reason VARCHAR(16), 
    raw_response TEXT, 
    input_tokens INTEGER DEFAULT '0' NOT NULL, 
    output_tokens INTEGER DEFAULT '0' NOT NULL, 
    cache_read_tokens INTEGER DEFAULT '0' NOT NULL, 
    cache_created_tokens INTEGER DEFAULT '0' NOT NULL, 
    latency_ms INTEGER, 
    entities_returned INTEGER DEFAULT '0' NOT NULL, 
    entities_kept INTEGER DEFAULT '0' NOT NULL, 
    relationships_returned INTEGER DEFAULT '0' NOT NULL, 
    relationships_kept INTEGER DEFAULT '0' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_extraction_calls PRIMARY KEY (id), 
    CONSTRAINT fk_extraction_calls_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_extraction_calls_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_extraction_calls_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL, 
    CONSTRAINT uq_extraction_calls_run_id_chunk_id UNIQUE (run_id, chunk_id), 
    CONSTRAINT ck_extraction_calls_ck_extraction_calls_status CHECK (status in ('accepted','rejected'))
);

CREATE INDEX ix_extraction_calls_run_id ON extraction_calls (run_id);

CREATE INDEX ix_extraction_calls_company_id ON extraction_calls (company_id);

CREATE INDEX ix_extraction_calls_document_id ON extraction_calls (document_id);

CREATE INDEX ix_extraction_calls_chunk_id ON extraction_calls (chunk_id);

CREATE INDEX ix_extraction_calls_run_id_status ON extraction_calls (run_id, status);

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';


CREATE TABLE extraction_runs (
    id UUID NOT NULL, 
    provider VARCHAR(64) NOT NULL, 
    model VARCHAR(64) NOT NULL, 
    prompt_id VARCHAR(64), 
    document_id BIGINT NOT NULL, 
    chunks_processed INTEGER DEFAULT '0' NOT NULL, 
    chunks_accepted INTEGER DEFAULT '0' NOT NULL, 
    chunks_rejected INTEGER DEFAULT '0' NOT NULL, 
    cached_input_tokens INTEGER DEFAULT '0' NOT NULL, 
    input_tokens INTEGER DEFAULT '0' NOT NULL, 
    output_tokens INTEGER DEFAULT '0' NOT NULL, 
    cost_usd NUMERIC(12, 6) DEFAULT '0' NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_extraction_runs PRIMARY KEY (id), 
    CONSTRAINT fk_extraction_runs_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT ck_extraction_runs_ck_extraction_runs_chunk_counts CHECK (chunks_accepted + chunks_rejected <= chunks_processed), 
    CONSTRAINT ck_extraction_runs_ck_extraction_runs_cost_usd CHECK (cost_usd >= 0)
);

CREATE INDEX ix_extraction_runs_document_id ON extraction_runs (document_id);

CREATE INDEX ix_extraction_runs_document_id_started_at ON extraction_runs (document_id, started_at);

ALTER TABLE extraction_calls ADD CONSTRAINT fk_extraction_calls_run_id_extraction_runs FOREIGN KEY(run_id) REFERENCES extraction_runs (id) ON DELETE CASCADE;

UPDATE alembic_version SET version_num='0008' WHERE alembic_version.version_num = '0007';


ALTER TABLE entity_mentions DROP CONSTRAINT uq_entity_mentions_entity_id_chunk_id;

ALTER TABLE entity_mentions ADD CONSTRAINT uq_entity_mentions_entity_id_chunk_id_paragraph_id UNIQUE NULLS NOT DISTINCT (entity_id, chunk_id, paragraph_id);

UPDATE alembic_version SET version_num='0009' WHERE alembic_version.version_num = '0008';


ALTER TABLE chunks ADD COLUMN paragraph_boxes JSONB;

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0009';


ALTER TABLE documents ADD COLUMN source_path TEXT;

ALTER TABLE documents ADD COLUMN pdf_path TEXT;

ALTER TABLE documents ADD COLUMN rendered_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE entities ADD COLUMN highlight_color VARCHAR(7);

ALTER TABLE entities ADD CONSTRAINT ck_entities_highlight_color CHECK (highlight_color ~ '^#[0-9a-fA-F]{6}$');

CREATE TABLE document_pages (
    id BIGSERIAL NOT NULL, 
    document_id BIGINT NOT NULL, 
    page INTEGER NOT NULL, 
    width FLOAT NOT NULL, 
    height FLOAT NOT NULL, 
    image_path TEXT, 
    thumbnail_path TEXT, 
    paragraph_count INTEGER DEFAULT '0' NOT NULL, 
    rendered_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_document_pages PRIMARY KEY (id), 
    CONSTRAINT uq_document_pages_document_id_page UNIQUE (document_id, page), 
    CONSTRAINT fk_document_pages_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);

CREATE INDEX ix_document_pages_document_id ON document_pages (document_id);

UPDATE alembic_version SET version_num='0011' WHERE alembic_version.version_num = '0010';


CREATE TABLE organizations (
    id UUID NOT NULL, 
    slug VARCHAR(64) NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    is_internal BOOLEAN DEFAULT 'false' NOT NULL, 
    daily_requests_per_user INTEGER, 
    monthly_tokens BIGINT, 
    monthly_spend_usd NUMERIC(12, 2), 
    max_input_tokens INTEGER, 
    max_output_tokens INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_organizations PRIMARY KEY (id), 
    CONSTRAINT uq_organizations_slug UNIQUE (slug)
);

CREATE TABLE users (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    email VARCHAR(255) NOT NULL, 
    name VARCHAR(255), 
    role VARCHAR(16) DEFAULT 'member' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_users PRIMARY KEY (id), 
    CONSTRAINT ck_users_role CHECK (role in ('member','org_admin','platform_admin')), 
    CONSTRAINT fk_users_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE INDEX ix_users_organization_id ON users (organization_id);

CREATE TABLE api_keys (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    key_hash VARCHAR(64) NOT NULL, 
    prefix VARCHAR(16) NOT NULL, 
    name VARCHAR(255), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    last_used_at TIMESTAMP WITH TIME ZONE, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_api_keys PRIMARY KEY (id), 
    CONSTRAINT fk_api_keys_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_api_keys_key_hash UNIQUE (key_hash)
);

CREATE INDEX ix_api_keys_user_id ON api_keys (user_id);

CREATE TABLE ai_usage_logs (
    id UUID NOT NULL, 
    user_id UUID, 
    organization_id UUID NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    model VARCHAR(128) NOT NULL, 
    task_type VARCHAR(64) NOT NULL, 
    request_id UUID NOT NULL, 
    input_tokens INTEGER DEFAULT '0' NOT NULL, 
    output_tokens INTEGER DEFAULT '0' NOT NULL, 
    cached_tokens INTEGER DEFAULT '0' NOT NULL, 
    estimated_cost_usd NUMERIC(12, 6) DEFAULT '0' NOT NULL, 
    latency_ms INTEGER DEFAULT '0' NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    error_message TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_ai_usage_logs PRIMARY KEY (id), 
    CONSTRAINT ck_ai_usage_logs_status CHECK (status in ('success','failed','fallback','cached','blocked')), 
    CONSTRAINT fk_ai_usage_logs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, 
    CONSTRAINT fk_ai_usage_logs_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
);

CREATE INDEX ix_ai_usage_logs_organization_id_created_at ON ai_usage_logs (organization_id, created_at);

CREATE INDEX ix_ai_usage_logs_user_id_created_at ON ai_usage_logs (user_id, created_at);

CREATE INDEX ix_ai_usage_logs_created_at ON ai_usage_logs (created_at);

CREATE INDEX ix_ai_usage_logs_request_id ON ai_usage_logs (request_id);

CREATE TABLE ai_response_cache (
    id BIGSERIAL NOT NULL, 
    cache_key VARCHAR(64) NOT NULL, 
    accession VARCHAR(32) NOT NULL, 
    task_type VARCHAR(64) NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    model VARCHAR(128) NOT NULL, 
    prompt_version VARCHAR(64) NOT NULL, 
    input_hash VARCHAR(64) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    response_text TEXT NOT NULL, 
    stop_reason VARCHAR(16) NOT NULL, 
    usage JSONB DEFAULT '{}' NOT NULL, 
    estimated_cost_usd NUMERIC(12, 6) DEFAULT '0' NOT NULL, 
    hit_count INTEGER DEFAULT '0' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE, 
    last_hit_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_ai_response_cache PRIMARY KEY (id), 
    CONSTRAINT uq_ai_response_cache_cache_key UNIQUE (cache_key)
);

CREATE INDEX ix_ai_response_cache_accession ON ai_response_cache (accession);

INSERT INTO organizations (id, slug, name, is_internal) VALUES (gen_random_uuid(), 'evident-internal', 'Evident (internal)', true);

UPDATE alembic_version SET version_num='0012' WHERE alembic_version.version_num = '0011';


CREATE TABLE company_fetches (
    id BIGSERIAL NOT NULL, 
    cik VARCHAR(10) NOT NULL, 
    ticker VARCHAR(16), 
    name VARCHAR(255) NOT NULL, 
    status VARCHAR(16) DEFAULT 'queued' NOT NULL, 
    error TEXT, 
    accession VARCHAR(32), 
    form_type VARCHAR(16), 
    chunks INTEGER, 
    requested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_company_fetches PRIMARY KEY (id), 
    CONSTRAINT ck_company_fetches_ck_company_fetches_status CHECK (status in ('queued','running','done','failed'))
);

CREATE UNIQUE INDEX uq_company_fetches_active_cik ON company_fetches (cik) WHERE status in ('queued','running');

CREATE INDEX ix_company_fetches_cik_requested_at ON company_fetches (cik, requested_at);

UPDATE alembic_version SET version_num='0013' WHERE alembic_version.version_num = '0012';


UPDATE organizations SET name = 'Parallx (internal)' WHERE slug = 'evident-internal' AND name = 'Evident (internal)';

UPDATE alembic_version SET version_num='0014' WHERE alembic_version.version_num = '0013';


UPDATE organizations SET slug = 'parallx-internal' WHERE slug = 'evident-internal';

UPDATE alembic_version SET version_num='0015' WHERE alembic_version.version_num = '0014';


ALTER TABLE company_fetches ADD COLUMN source VARCHAR(16) DEFAULT 'search' NOT NULL;

ALTER TABLE company_fetches ADD CONSTRAINT ck_company_fetches_source CHECK (source in ('search','backfill','watch'));

CREATE TABLE sync_runs (
    id BIGSERIAL NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    seen INTEGER DEFAULT '0' NOT NULL, 
    "new" INTEGER DEFAULT '0' NOT NULL, 
    fetched INTEGER DEFAULT '0' NOT NULL, 
    failed INTEGER DEFAULT '0' NOT NULL, 
    error TEXT, 
    CONSTRAINT pk_sync_runs PRIMARY KEY (id), 
    CONSTRAINT ck_sync_runs_kind CHECK (kind in ('poll','sweep'))
);

CREATE INDEX ix_sync_runs_kind_started_at ON sync_runs (kind, started_at);

UPDATE alembic_version SET version_num='0016' WHERE alembic_version.version_num = '0015';


ALTER TABLE companies ADD COLUMN filings_indexed_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE companies ADD COLUMN facts_loaded_at TIMESTAMP WITH TIME ZONE;

CREATE TABLE filings (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    accession VARCHAR(32) NOT NULL, 
    form_type VARCHAR(16) NOT NULL, 
    filed_date DATE NOT NULL, 
    accepted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    report_date DATE, 
    primary_document TEXT, 
    description TEXT, 
    items VARCHAR(128), 
    size INTEGER, 
    is_xbrl BOOLEAN DEFAULT 'false' NOT NULL, 
    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    alerted_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_filings PRIMARY KEY (id), 
    CONSTRAINT fk_filings_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_filings_accession UNIQUE (accession)
);

CREATE INDEX ix_filings_company_id_accepted_at ON filings (company_id, accepted_at);

CREATE INDEX ix_filings_accepted_at ON filings (accepted_at);

CREATE TABLE financial_facts (
    id BIGSERIAL NOT NULL, 
    company_id BIGINT NOT NULL, 
    concept VARCHAR(160) NOT NULL, 
    unit VARCHAR(32) NOT NULL, 
    period_start DATE, 
    period_end DATE NOT NULL, 
    value NUMERIC(28, 6) NOT NULL, 
    accession VARCHAR(32) NOT NULL, 
    form VARCHAR(16) NOT NULL, 
    filed DATE NOT NULL, 
    fiscal_year SMALLINT, 
    fiscal_period VARCHAR(4), 
    frame VARCHAR(16) NOT NULL, 
    CONSTRAINT pk_financial_facts PRIMARY KEY (id), 
    CONSTRAINT uq_financial_facts_company_id_concept_unit_frame UNIQUE (company_id, concept, unit, frame), 
    CONSTRAINT fk_financial_facts_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_financial_facts_company_id_period_end ON financial_facts (company_id, period_end);

UPDATE alembic_version SET version_num='0017' WHERE alembic_version.version_num = '0016';


ALTER TABLE users ALTER COLUMN email DROP NOT NULL;

ALTER TABLE users ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE users ADD COLUMN auth_uid VARCHAR(128);

ALTER TABLE users ADD COLUMN auth_provider VARCHAR(16);

ALTER TABLE users ADD COLUMN email_normalized VARCHAR(255);

ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT 'false' NOT NULL;

ALTER TABLE users ADD COLUMN phone VARCHAR(20);

ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE users ADD COLUMN billing_customer_id VARCHAR(255);

ALTER TABLE users ADD CONSTRAINT uq_users_auth_uid UNIQUE (auth_uid);

ALTER TABLE users ADD CONSTRAINT uq_users_email_normalized UNIQUE (email_normalized);

ALTER TABLE users ADD CONSTRAINT uq_users_phone UNIQUE (phone);

ALTER TABLE users ADD CONSTRAINT uq_users_billing_customer_id UNIQUE (billing_customer_id);

CREATE TABLE sessions (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    token_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    idle_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    user_agent VARCHAR(255), 
    CONSTRAINT pk_sessions PRIMARY KEY (id), 
    CONSTRAINT fk_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash)
);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE plans (
    id VARCHAR(32) NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    audience VARCHAR(128) NOT NULL, 
    monthly_price INTEGER NOT NULL, 
    annual_price INTEGER NOT NULL, 
    max_companies INTEGER NOT NULL, 
    monthly_ai_queries INTEGER NOT NULL, 
    entitlements JSONB NOT NULL, 
    sort INTEGER DEFAULT '0' NOT NULL, 
    CONSTRAINT pk_plans PRIMARY KEY (id)
);

INSERT INTO plans (id, name, audience, monthly_price, annual_price, max_companies, monthly_ai_queries, entitlements, sort) VALUES ('free', 'Free', 'Try Parallx on two companies', 0, 0, 2, 10, '{"promise_tracker": "basic", "timelines": "basic", "evidence_viewer": true, "weekly_alerts": false, "daily_monitoring": false, "advanced_comparisons": false, "exports": false, "transcripts": false}'::jsonb, 0);

INSERT INTO plans (id, name, audience, monthly_price, annual_price, max_companies, monthly_ai_queries, entitlements, sort) VALUES ('investor', 'Iix', 'For investors', 1900, 19000, 10, 100, '{"promise_tracker": "full", "timelines": "full", "evidence_viewer": true, "weekly_alerts": true, "daily_monitoring": false, "advanced_comparisons": false, "exports": false, "transcripts": false}'::jsonb, 1);

INSERT INTO plans (id, name, audience, monthly_price, annual_price, max_companies, monthly_ai_queries, entitlements, sort) VALUES ('analyst', 'IIx', 'For analysts', 3900, 39000, 50, 500, '{"promise_tracker": "full", "timelines": "full", "evidence_viewer": true, "weekly_alerts": true, "daily_monitoring": true, "advanced_comparisons": true, "exports": true, "transcripts": "where_licensed"}'::jsonb, 2);

CREATE TABLE subscriptions (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    plan_id VARCHAR(32) NOT NULL, 
    billing_provider VARCHAR(16) NOT NULL, 
    provider_customer_id VARCHAR(255) NOT NULL, 
    provider_subscription_id VARCHAR(255) NOT NULL, 
    provider_price_id VARCHAR(255), 
    status VARCHAR(24) NOT NULL, 
    billing_interval VARCHAR(8) NOT NULL, 
    current_period_start TIMESTAMP WITH TIME ZONE, 
    current_period_end TIMESTAMP WITH TIME ZONE, 
    cancel_at_period_end BOOLEAN DEFAULT 'false' NOT NULL, 
    canceled_at TIMESTAMP WITH TIME ZONE, 
    requires_action BOOLEAN DEFAULT 'false' NOT NULL, 
    last_event_id VARCHAR(255), 
    last_event_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_subscriptions PRIMARY KEY (id), 
    CONSTRAINT ck_subscriptions_status CHECK (status in ('incomplete','incomplete_expired','trialing','active','past_due','canceled','unpaid','paused')), 
    CONSTRAINT ck_subscriptions_billing_interval CHECK (billing_interval in ('month','year')), 
    CONSTRAINT fk_subscriptions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT fk_subscriptions_plan_id_plans FOREIGN KEY(plan_id) REFERENCES plans (id), 
    CONSTRAINT uq_subscriptions_provider_subscription_id UNIQUE (provider_subscription_id)
);

CREATE INDEX ix_subscriptions_user_id ON subscriptions (user_id);

CREATE TABLE user_company_access (
    id BIGSERIAL NOT NULL, 
    user_id UUID NOT NULL, 
    company_id BIGINT NOT NULL, 
    first_researched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    last_accessed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    access_source VARCHAR(16) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    CONSTRAINT pk_user_company_access PRIMARY KEY (id), 
    CONSTRAINT uq_user_company_access_user_id_company_id UNIQUE (user_id, company_id), 
    CONSTRAINT ck_user_company_access_status CHECK (status in ('pending','active','refunded')), 
    CONSTRAINT ck_user_company_access_access_source CHECK (access_source in ('free_credit','plan','staff')), 
    CONSTRAINT fk_user_company_access_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT fk_user_company_access_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_user_company_access_user_id_last_accessed_at ON user_company_access (user_id, last_accessed_at);

CREATE TABLE usage_ledger (
    id BIGSERIAL NOT NULL, 
    user_id UUID NOT NULL, 
    kind VARCHAR(24) NOT NULL, 
    quantity INTEGER NOT NULL, 
    company_id BIGINT, 
    subscription_id UUID, 
    period_start TIMESTAMP WITH TIME ZONE, 
    period_end TIMESTAMP WITH TIME ZONE, 
    idempotency_key VARCHAR(160), 
    detail JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_usage_ledger PRIMARY KEY (id), 
    CONSTRAINT ck_usage_ledger_kind CHECK (kind in ('free_company_credit','paid_company','ai_query','period_reset','refund')), 
    CONSTRAINT fk_usage_ledger_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT fk_usage_ledger_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE SET NULL, 
    CONSTRAINT fk_usage_ledger_subscription_id_subscriptions FOREIGN KEY(subscription_id) REFERENCES subscriptions (id) ON DELETE SET NULL, 
    CONSTRAINT uq_usage_ledger_idempotency_key UNIQUE (idempotency_key)
);

CREATE INDEX ix_usage_ledger_user_id_kind_created_at ON usage_ledger (user_id, kind, created_at);

CREATE TABLE billing_events (
    id VARCHAR(255) NOT NULL, 
    provider VARCHAR(16) NOT NULL, 
    type VARCHAR(64) NOT NULL, 
    event_created_at TIMESTAMP WITH TIME ZONE, 
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    outcome VARCHAR(16) NOT NULL, 
    CONSTRAINT pk_billing_events PRIMARY KEY (id)
);

CREATE TABLE idempotency_keys (
    id BIGSERIAL NOT NULL, 
    user_id UUID NOT NULL, 
    scope VARCHAR(32) NOT NULL, 
    key VARCHAR(128) NOT NULL, 
    response JSONB NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_idempotency_keys PRIMARY KEY (id), 
    CONSTRAINT uq_idempotency_keys_user_id_scope_key UNIQUE (user_id, scope, key), 
    CONSTRAINT fk_idempotency_keys_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE rate_limits (
    bucket VARCHAR(200) NOT NULL, 
    window_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    count INTEGER DEFAULT '0' NOT NULL, 
    CONSTRAINT pk_rate_limits PRIMARY KEY (bucket, window_start)
);

UPDATE alembic_version SET version_num='0018' WHERE alembic_version.version_num = '0017';

COMMIT;
