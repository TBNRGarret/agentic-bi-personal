CREATE DATABASE IF NOT EXISTS vsf_raw;
CREATE DATABASE IF NOT EXISTS vsf_stg;
CREATE DATABASE IF NOT EXISTS vsf_mart;

CREATE TABLE IF NOT EXISTS vsf_raw.raw_ingestion_batches (
    batch_id UUID,
    source_app_id String,
    source_table String,
    object_path Nullable(String),
    extract_started_at Nullable(DateTime64(6, 'UTC')),
    extract_finished_at Nullable(DateTime64(6, 'UTC')),
    load_started_at Nullable(DateTime64(6, 'UTC')),
    load_finished_at Nullable(DateTime64(6, 'UTC')),
    row_count Int64,
    status String,
    error_message Nullable(String),
    created_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMMDD(created_at)
ORDER BY (created_at, batch_id, source_app_id, source_table);

CREATE TABLE IF NOT EXISTS vsf_raw.signoz_logs (
    log_id String,
    timestamp DateTime64(6, 'UTC'),
    trace_id String,
    span_id String,
    service_name String,
    severity String,
    message String,
    raw_payload String,
    source_app_id String,
    batch_id UUID,
    ingested_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (timestamp, source_app_id, log_id);

CREATE TABLE IF NOT EXISTS vsf_raw.signoz_traces (
    trace_id String,
    span_id String,
    timestamp DateTime64(6, 'UTC'),
    service_name String,
    name String,
    duration_ms Int64,
    raw_payload String,
    source_app_id String,
    batch_id UUID,
    ingested_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (timestamp, source_app_id, trace_id, span_id);

CREATE TABLE IF NOT EXISTS vsf_raw.signoz_metrics (
    metric_id String,
    timestamp DateTime64(6, 'UTC'),
    service_name String,
    metric_name String,
    metric_value Float64,
    unit String,
    attributes String,
    raw_payload String,
    source_app_id String,
    batch_id UUID,
    ingested_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (timestamp, source_app_id, metric_name, metric_id);

CREATE TABLE IF NOT EXISTS vsf_raw.langfuse_generations (
    generation_id String,
    trace_id String,
    timestamp DateTime64(6, 'UTC'),
    model String,
    prompt_text String,
    response_text String,
    input_tokens Int32,
    output_tokens Int32,
    total_tokens Int32,
    estimated_cost_usd Decimal(12, 6),
    duration_ms Int64,
    status String,
    raw_payload String,
    source_app_id String,
    batch_id UUID,
    ingested_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (timestamp, source_app_id, generation_id);

CREATE TABLE IF NOT EXISTS vsf_raw.user_interactions (
    interaction_id String,
    langfuse_trace_id String,
    session_id String,
    user_id String,
    started_at DateTime64(6, 'UTC'),
    ended_at DateTime64(6, 'UTC'),
    model String,
    prompt_text String,
    response_text String,
    input_tokens Int32,
    output_tokens Int32,
    total_tokens Int32,
    estimated_cost_usd Decimal(12, 6),
    status String,
    raw_payload String,
    source_app_id String,
    batch_id UUID,
    ingested_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMMDD(started_at)
ORDER BY (started_at, source_app_id, interaction_id);

CREATE TABLE IF NOT EXISTS vsf_stg.staging_llm_generations (
    generation_id String,
    trace_id String,
    source_app_id String,
    domain Nullable(String),
    environment Nullable(String),
    user_id_hash Nullable(String),
    session_id Nullable(String),
    model String,
    provider String,
    prompt_tokens Int32,
    completion_tokens Int32,
    total_tokens Int32,
    input_cost_usd Decimal(18, 8),
    output_cost_usd Decimal(18, 8),
    total_cost_usd Decimal(18, 8),
    latency_ms Int32,
    status String,
    error_type Nullable(String),
    started_at DateTime64(6, 'UTC'),
    ended_at DateTime64(6, 'UTC'),
    batch_id UUID,
    loaded_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
ORDER BY (source_app_id, generation_id);

CREATE TABLE IF NOT EXISTS vsf_mart.source_apps (
    source_app_id String,
    source_app_name String,
    domain String,
    environment String,
    owner_team String,
    source_dwh_type String,
    source_dwh_name String,
    is_active UInt8,
    created_at DateTime64(6, 'UTC'),
    updated_at DateTime64(6, 'UTC')
) ENGINE = ReplacingMergeTree()
ORDER BY source_app_id;

CREATE OR REPLACE VIEW vsf_stg.stg_langfuse_generations AS SELECT * FROM vsf_raw.langfuse_generations;
CREATE OR REPLACE VIEW vsf_stg.stg_user_interactions AS SELECT * FROM vsf_raw.user_interactions;
CREATE OR REPLACE VIEW vsf_stg.stg_signoz_logs AS SELECT * FROM vsf_raw.signoz_logs;
