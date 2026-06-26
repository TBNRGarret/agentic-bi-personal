CREATE DATABASE IF NOT EXISTS gold;
CREATE DATABASE IF NOT EXISTS silver;

-- ── Silver Views (thay thế dbt silver models) ─────────────────────────────

CREATE OR REPLACE VIEW silver.silver_user_interactions AS
SELECT
    interaction_id,
    langfuse_trace_id,
    session_id,
    user_id,
    if(nullIf(user_id, '') IS NULL, NULL, lower(hex(MD5(user_id)))) AS user_id_hash,
    started_at,
    ended_at,
    model,
    multiIf(
        lower(model) LIKE '%gpt%' OR lower(model) LIKE '%openai%', 'openai',
        lower(model) LIKE '%gemini%', 'google',
        lower(model) LIKE '%claude%', 'anthropic',
        'unknown'
    ) AS provider,
    prompt_text,
    response_text,
    input_tokens,
    output_tokens,
    total_tokens,
    toDecimal64(estimated_cost_usd, 8) AS total_cost_usd,
    toInt32(dateDiff('millisecond', started_at, ended_at)) AS latency_ms,
    status,
    raw_payload,
    source_app_id,
    batch_id,
    ingested_at
FROM vsf_raw.user_interactions;

CREATE OR REPLACE VIEW silver.silver_langfuse_generations AS
SELECT * FROM vsf_raw.langfuse_generations;

CREATE OR REPLACE VIEW silver.silver_signoz_logs AS
SELECT * FROM vsf_raw.signoz_logs;

CREATE OR REPLACE VIEW silver.silver_signoz_traces AS
SELECT * FROM vsf_raw.signoz_traces;

CREATE OR REPLACE VIEW silver.silver_signoz_metrics AS
SELECT * FROM vsf_raw.signoz_metrics;

CREATE OR REPLACE VIEW silver.silver_staging_llm_generations AS
SELECT
    g.generation_id,
    g.trace_id,
    g.source_app_id,
    nullIf(app.domain, '') AS domain,
    nullIf(app.environment, '') AS environment,
    if(nullIf(i.user_id, '') IS NULL, NULL, lower(hex(MD5(i.user_id)))) AS user_id_hash,
    nullIf(i.session_id, '') AS session_id,
    g.model,
    multiIf(
        lower(g.model) LIKE '%gpt%', 'openai',
        lower(g.model) LIKE '%gemini%', 'google',
        lower(g.model) LIKE '%claude%', 'anthropic',
        'unknown'
    ) AS provider,
    g.input_tokens AS prompt_tokens,
    g.output_tokens AS completion_tokens,
    g.total_tokens,
    toDecimal64(if(g.total_tokens > 0, toFloat64(g.estimated_cost_usd) * g.input_tokens / g.total_tokens, 0), 8) AS input_cost_usd,
    toDecimal64(if(g.total_tokens > 0, toFloat64(g.estimated_cost_usd) * g.output_tokens / g.total_tokens, 0), 8) AS output_cost_usd,
    toDecimal64(g.estimated_cost_usd, 8) AS total_cost_usd,
    toInt32(g.duration_ms) AS latency_ms,
    g.status,
    if(g.status = 'failed', 'generation_failed', NULL) AS error_type,
    coalesce(i.started_at, g.timestamp) AS started_at,
    coalesce(i.ended_at, g.timestamp + toIntervalMillisecond(g.duration_ms)) AS ended_at,
    g.batch_id,
    g.ingested_at AS loaded_at
FROM vsf_raw.langfuse_generations g
LEFT JOIN vsf_raw.user_interactions i ON g.trace_id = i.langfuse_trace_id AND g.source_app_id = i.source_app_id
LEFT JOIN vsf_mart.source_apps app ON g.source_app_id = app.source_app_id;

-- ── Gold Mart Views (thay thế dbt gold models) ────────────────────────────

CREATE OR REPLACE VIEW vsf_mart.fct_llm_interactions AS
WITH interactions AS (
    SELECT * FROM silver.silver_user_interactions
),
logs AS (
    SELECT
        source_app_id,
        trace_id,
        groupArray(message) AS error_messages,
        count() AS error_log_count
    FROM silver.silver_signoz_logs
    WHERE severity IN ('ERROR', 'FATAL') AND trace_id != ''
    GROUP BY source_app_id, trace_id
),
source_apps AS (
    SELECT * FROM vsf_mart.source_apps
)
SELECT
    i.interaction_id,
    i.langfuse_trace_id,
    i.session_id,
    i.user_id_hash,
    i.source_app_id AS source_app_id,
    nullIf(app.domain, '') AS domain,
    nullIf(app.environment, '') AS environment,
    i.started_at,
    i.ended_at,
    i.model,
    i.provider,
    i.prompt_text,
    i.response_text,
    i.input_tokens,
    i.output_tokens,
    i.total_tokens,
    i.total_cost_usd,
    i.latency_ms,
    i.status,
    COALESCE(l.error_log_count, 0) AS error_log_count,
    arrayStringConcat(l.error_messages, '; ') AS error_details,
    i.batch_id,
    i.ingested_at
FROM interactions i
LEFT JOIN logs l ON i.langfuse_trace_id = l.trace_id AND i.source_app_id = l.source_app_id
LEFT JOIN source_apps app ON i.source_app_id = app.source_app_id;

CREATE OR REPLACE VIEW vsf_mart.gold_app_daily_cost AS
SELECT
    toDate(started_at) AS date,
    source_app_id,
    domain,
    environment,
    provider,
    model,
    count() AS request_count,
    sum(total_tokens) AS total_tokens,
    sum(total_cost_usd) AS total_cost_usd,
    avg(latency_ms) AS avg_latency_ms,
    quantile(0.95)(latency_ms) AS p95_latency_ms,
    sum(if(status = 'failed' OR error_log_count > 0, 1, 0)) AS error_count,
    error_count / request_count AS error_rate
FROM vsf_mart.fct_llm_interactions
GROUP BY date, source_app_id, domain, environment, provider, model;

-- ── Existing Gold Views ────────────────────────────────────────────────────

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

-- ── Updated Silver Views (trước đây ref dbt staging, giờ ref silver.*) ──

CREATE OR REPLACE VIEW silver.silver_llm_generations AS
SELECT
    timestamp AS event_time,
    model,
    estimated_cost_usd AS cost_usd,
    total_tokens,
    duration_ms AS latency_ms,
    status AS generation_status,
    source_app_id AS source_app
FROM silver.silver_langfuse_generations;

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
FROM silver.silver_signoz_logs;
