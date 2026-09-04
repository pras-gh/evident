-- evident — full schema, generated from the Alembic chain.
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


CREATE TABLE extraction_runs (
    id BIGSERIAL NOT NULL, 
    run_id VARCHAR(64) NOT NULL, 
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
    CONSTRAINT pk_extraction_runs PRIMARY KEY (id), 
    CONSTRAINT fk_extraction_runs_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_extraction_runs_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_extraction_runs_chunk_id_chunks FOREIGN KEY(chunk_id) REFERENCES chunks (id) ON DELETE SET NULL, 
    CONSTRAINT uq_extraction_runs_run_id_chunk_id UNIQUE (run_id, chunk_id), 
    CONSTRAINT ck_extraction_runs_ck_extraction_runs_status CHECK (status in ('accepted','rejected'))
);

CREATE INDEX ix_extraction_runs_run_id ON extraction_runs (run_id);

CREATE INDEX ix_extraction_runs_company_id ON extraction_runs (company_id);

CREATE INDEX ix_extraction_runs_document_id ON extraction_runs (document_id);

CREATE INDEX ix_extraction_runs_chunk_id ON extraction_runs (chunk_id);

CREATE INDEX ix_extraction_runs_run_id_status ON extraction_runs (run_id, status);

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';

COMMIT;
