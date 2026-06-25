{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(date)',
    order_by='(date, model)'
) }}

SELECT
    toDate(started_at) AS date,
    source_app_id,
    model,
    count() AS total_requests,
    sum(case when status = 'failed' or error_log_count > 0 then 1 else 0 end) AS failed_requests,
    round(failed_requests / total_requests * 100, 2) AS error_rate_percent,
    sum(total_tokens) AS total_tokens_used,
    avg(total_tokens) AS avg_tokens_per_request,
    sum(total_cost_usd) AS total_cost_usd,
    avg(f.total_cost_usd) AS avg_cost_per_request,
    avg(latency_ms) AS avg_latency_ms
FROM {{ ref('fct_llm_interactions') }} AS f
GROUP BY date, source_app_id, model
