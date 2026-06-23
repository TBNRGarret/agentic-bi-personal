-- Migration: Add PARTITION BY toYYYYMM(timestamp) to signoz tables.
--
-- Strategy: DROP + CREATE (data is ~tens of K rows, will be re-seeded).
-- Run this ONCE when pipeline is idle, then re-run seed_minio.py + trigger pipeline.
--
-- Usage:
--   clickhouse-client --multiquery < include/clickhouse/migrate_add_partitions.sql

-- 1. signoz_logs
DROP TABLE IF EXISTS vsf_raw.signoz_logs;
CREATE TABLE vsf_raw.signoz_logs (
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
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, source_app_id, log_id);

-- 2. signoz_traces
DROP TABLE IF EXISTS vsf_raw.signoz_traces;
CREATE TABLE vsf_raw.signoz_traces (
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
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, source_app_id, trace_id, span_id);

-- 3. signoz_metrics
DROP TABLE IF EXISTS vsf_raw.signoz_metrics;
CREATE TABLE vsf_raw.signoz_metrics (
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
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, source_app_id, metric_name, metric_id);
