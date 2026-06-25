{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(date)',
    order_by='(date, source_app_id, provider, model)'
) }}

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
FROM {{ ref('fct_llm_interactions') }}
GROUP BY
    date,
    source_app_id,
    domain,
    environment,
    provider,
    model
