{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(started_at)',
    order_by='interaction_id'
) }}

WITH interactions AS (
    SELECT * FROM {{ ref('stg_user_interactions') }}
),
logs AS (
    SELECT
        source_app_id,
        trace_id,
        groupArray(message) AS error_messages,
        count() AS error_log_count
    FROM {{ ref('stg_signoz_logs') }}
    WHERE severity IN ('ERROR', 'FATAL') AND trace_id != ''
    GROUP BY source_app_id, trace_id
),
source_apps AS (
    SELECT * FROM {{ ref('source_apps') }}
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
LEFT JOIN logs l
    ON i.langfuse_trace_id = l.trace_id
    AND i.source_app_id = l.source_app_id
LEFT JOIN source_apps app
    ON i.source_app_id = app.source_app_id
