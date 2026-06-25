{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(started_at)',
    order_by='(source_app_id, generation_id)'
) }}

WITH generations AS (
    SELECT * FROM {{ source('raw', 'langfuse_generations') }}
),
interactions AS (
    SELECT * FROM {{ source('raw', 'user_interactions') }}
),
source_apps AS (
    SELECT * FROM {{ ref('source_apps') }}
)
SELECT
    g.generation_id,
    g.trace_id,
    g.source_app_id AS source_app_id,
    nullIf(app.domain, '') AS domain,
    nullIf(app.environment, '') AS environment,
    if(
        nullIf(i.user_id, '') IS NULL,
        CAST(NULL, 'Nullable(String)'),
        lower(hex(MD5(i.user_id)))
    ) AS user_id_hash,
    nullIf(i.session_id, '') AS session_id,
    g.model,
    multiIf(
        lower(g.model) LIKE '%gpt%' OR lower(g.model) LIKE '%openai%', 'openai',
        lower(g.model) LIKE '%gemini%', 'google',
        lower(g.model) LIKE '%claude%', 'anthropic',
        'unknown'
    ) AS provider,
    g.input_tokens AS prompt_tokens,
    g.output_tokens AS completion_tokens,
    g.total_tokens,
    toDecimal64(
        if(g.total_tokens > 0, toFloat64(g.estimated_cost_usd) * g.input_tokens / g.total_tokens, 0),
        8
    ) AS input_cost_usd,
    toDecimal64(
        if(g.total_tokens > 0, toFloat64(g.estimated_cost_usd) * g.output_tokens / g.total_tokens, 0),
        8
    ) AS output_cost_usd,
    toDecimal64(g.estimated_cost_usd, 8) AS total_cost_usd,
    toInt32(g.duration_ms) AS latency_ms,
    g.status,
    if(g.status = 'failed', 'generation_failed', CAST(NULL, 'Nullable(String)')) AS error_type,
    coalesce(i.started_at, g.timestamp) AS started_at,
    coalesce(i.ended_at, g.timestamp + toIntervalMillisecond(g.duration_ms)) AS ended_at,
    g.batch_id,
    g.ingested_at AS loaded_at
FROM generations g
LEFT JOIN interactions i
    ON g.trace_id = i.langfuse_trace_id
    AND g.source_app_id = i.source_app_id
LEFT JOIN source_apps app
    ON g.source_app_id = app.source_app_id
