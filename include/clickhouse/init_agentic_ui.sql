CREATE DATABASE IF NOT EXISTS gold;
CREATE DATABASE IF NOT EXISTS silver;

CREATE OR REPLACE VIEW gold.gold_llm_usage_daily AS
SELECT
    date AS metric_date,
    source_app_id AS source_app,
    model,
    request_count AS generation_count,
    request_count - error_count AS success_count,
    error_count,
    total_tokens,
    total_cost_usd,
    avg_latency_ms,
    p95_latency_ms
FROM vsf_mart.gold_app_daily_cost;

CREATE OR REPLACE VIEW gold.gold_trace_health_daily AS
SELECT
    toDate(started_at) AS metric_date,
    source_app_id AS source_app,
    count() AS trace_count,
    countIf(lower(status) IN ('success', 'ok', 'completed')) AS success_count,
    countIf(lower(status) NOT IN ('success', 'ok', 'completed')) AS error_count,
    avg(latency_ms) AS avg_latency_ms,
    quantile(0.95)(latency_ms) AS p95_latency_ms
FROM vsf_mart.fct_llm_interactions
GROUP BY metric_date, source_app;

CREATE OR REPLACE VIEW gold.gold_ingestion_health_daily AS
SELECT
    toDate(created_at) AS metric_date,
    source_app_id AS source_app,
    count() AS batch_count,
    countIf(lower(status) IN ('loaded', 'success', 'completed')) AS loaded_batch_count,
    countIf(lower(status) IN ('failed', 'error')) AS failed_batch_count,
    countIf(lower(status) IN ('quarantined', 'quarantine')) AS quarantined_batch_count,
    sum(row_count) AS loaded_row_count
FROM vsf_raw.raw_ingestion_batches
GROUP BY metric_date, source_app;

CREATE OR REPLACE VIEW gold.gold_system_observability_daily AS
SELECT
    traces.metric_date AS metric_date,
    traces.source_app AS source_app,
    traces.service_name AS service_name,
    coalesce(logs.log_count, 0) AS log_count,
    coalesce(logs.error_log_count, 0) AS error_log_count,
    traces.span_count AS span_count,
    toUInt64(0) AS error_span_count,
    traces.avg_span_duration_ms AS avg_span_duration_ms,
    traces.p95_span_duration_ms AS p95_span_duration_ms
FROM
(
    SELECT
        toDate(timestamp) AS metric_date,
        source_app_id AS source_app,
        service_name,
        count() AS span_count,
        avg(duration_ms) AS avg_span_duration_ms,
        quantile(0.95)(duration_ms) AS p95_span_duration_ms
    FROM vsf_raw.signoz_traces
    GROUP BY metric_date, source_app, service_name
) AS traces
LEFT JOIN
(
    SELECT
        toDate(timestamp) AS metric_date,
        source_app_id AS source_app,
        service_name,
        count() AS log_count,
        countIf(lower(severity) IN ('error', 'fatal', 'critical')) AS error_log_count
    FROM vsf_raw.signoz_logs
    GROUP BY metric_date, source_app, service_name
) AS logs
USING (metric_date, source_app, service_name);

CREATE OR REPLACE VIEW silver.silver_llm_generations AS
SELECT
    timestamp AS event_time,
    model,
    estimated_cost_usd AS cost_usd,
    total_tokens,
    duration_ms AS latency_ms,
    status AS generation_status,
    source_app_id AS source_app
FROM vsf_stg.stg_langfuse_generations;

CREATE OR REPLACE VIEW silver.silver_system_traces AS
SELECT
    timestamp AS event_time,
    source_app_id AS source_app,
    service_name,
    duration_ms,
    'unknown' AS span_status
FROM vsf_raw.signoz_traces;

CREATE OR REPLACE VIEW silver.silver_system_logs AS
SELECT
    timestamp AS event_time,
    source_app_id AS source_app,
    service_name,
    severity,
    message,
    trace_id,
    span_id
FROM vsf_stg.stg_signoz_logs;
