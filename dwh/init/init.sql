-- ==========================================
-- Agentic BI — DWH Initialization Script
-- Schemas: raw, silver, gold
-- ==========================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- ==========================================
-- TRACKING & VALIDATION TABLES
-- ==========================================

-- Registry: authorized data sources
CREATE TABLE IF NOT EXISTS raw.source_app_registry (
    source_app TEXT PRIMARY KEY,
    source_owner TEXT NOT NULL,
    source_env TEXT NOT NULL DEFAULT 'prod',
    description TEXT,
    expected_tables TEXT[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tracking: each ingest batch
CREATE TABLE IF NOT EXISTS raw.ingestion_batches (
    batch_id TEXT PRIMARY KEY,
    source_owner TEXT NOT NULL,
    target_table TEXT NOT NULL,
    object_path TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    declared_rows BIGINT,
    loaded_rows BIGINT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    _ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed registered sources
INSERT INTO raw.source_app_registry (source_app, source_owner, source_env, expected_tables) VALUES
    ('local_dwh', 'AN', 'prod', ARRAY['staging_llm_generations','staging_llm_traces','staging_system_logs','staging_system_traces']),
    ('bach_local_dwh', 'BACH', 'prod', ARRAY['staging_llm_generations','staging_llm_traces','staging_system_logs','staging_system_traces']),
    ('hoang_local_dwh', 'HOANG', 'prod', ARRAY['staging_llm_generations','staging_llm_traces','staging_system_logs','staging_system_traces','staging_system_metrics']),
    ('tuan_local_dwh', 'TUAN', 'prod', ARRAY['staging_llm_traces','staging_system_logs','staging_system_traces'])
ON CONFLICT (source_app) DO NOTHING;

-- ==========================================
-- RAW DATA TABLES (all columns as TEXT due to schema differences across owners)
-- ==========================================

-- LLM generations
CREATE TABLE IF NOT EXISTS raw.llm_generations (
    id TEXT PRIMARY KEY,
    langfuse_id TEXT,
    trace_id TEXT,
    generation_name TEXT,
    model TEXT,
    model_parameters TEXT,
    prompt TEXT,
    completion TEXT,
    prompt_tokens TEXT,
    completion_tokens TEXT,
    total_tokens TEXT,
    cost_usd TEXT,
    latency_ms TEXT,
    status TEXT,
    start_time TEXT,
    end_time TEXT,
    metadata TEXT,
    ingested_at TEXT,
    source_owner TEXT,
    _batch_id TEXT,
    _ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- LLM traces
CREATE TABLE IF NOT EXISTS raw.llm_traces (
    id TEXT PRIMARY KEY,
    langfuse_id TEXT,
    trace_name TEXT,
    user_id TEXT,
    session_id TEXT,
    input TEXT,
    output TEXT,
    metadata TEXT,
    tags TEXT,
    start_time TEXT,
    end_time TEXT,
    latency_ms TEXT,
    total_cost_usd TEXT,
    ingested_at TEXT,
    source_owner TEXT,
    _batch_id TEXT,
    _ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- System logs
CREATE TABLE IF NOT EXISTS raw.system_logs (
    id TEXT PRIMARY KEY,
    timestamp TEXT,
    severity_text TEXT,
    severity_number TEXT,
    body TEXT,
    service_name TEXT,
    trace_id TEXT,
    span_id TEXT,
    attributes TEXT,
    resource_attrs TEXT,
    ingested_at TEXT,
    source_owner TEXT,
    _batch_id TEXT,
    _ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- System traces
CREATE TABLE IF NOT EXISTS raw.system_traces (
    id TEXT PRIMARY KEY,
    trace_id TEXT,
    span_id TEXT,
    parent_span_id TEXT,
    service_name TEXT,
    operation_name TEXT,
    span_kind TEXT,
    status_code TEXT,
    status_message TEXT,
    duration_ns TEXT,
    start_time TEXT,
    end_time TEXT,
    attributes TEXT,
    resource_attrs TEXT,
    ingested_at TEXT,
    source_owner TEXT,
    _batch_id TEXT,
    _ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- System metrics (HOANG only)
CREATE TABLE IF NOT EXISTS raw.system_metrics (
    metric_id TEXT PRIMARY KEY,
    timestamp TEXT,
    service_name TEXT,
    metric_name TEXT,
    metric_value TEXT,
    unit TEXT,
    attributes TEXT,
    raw_payload TEXT,
    source_owner TEXT,
    _batch_id TEXT,
    _ingested_at TIMESTAMPTZ DEFAULT NOW()
);
