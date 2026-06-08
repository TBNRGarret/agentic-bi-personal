{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='interaction_id'
) }}

WITH interactions AS (
    SELECT * FROM {{ ref('stg_user_interactions') }}
),
logs AS (
    SELECT
        trace_id,
        groupArray(message) AS error_messages,
        count() AS error_log_count
    FROM {{ ref('stg_signoz_logs') }}
    WHERE severity IN ('ERROR', 'FATAL') AND trace_id != ''
    GROUP BY trace_id
)
SELECT
    i.interaction_id,
    i.langfuse_trace_id,
    i.session_id,
    i.user_id,
    i.started_at,
    i.ended_at,
    i.model,
    i.prompt_text,
    i.response_text,
    i.input_tokens,
    i.output_tokens,
    i.total_tokens,
    i.estimated_cost_usd,
    i.status,
    COALESCE(l.error_log_count, 0) AS error_log_count,
    arrayStringConcat(l.error_messages, '; ') AS error_details,
    i.ingested_at
FROM interactions i
LEFT JOIN logs l ON i.langfuse_trace_id = l.trace_id
