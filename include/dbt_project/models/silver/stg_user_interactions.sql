{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(started_at)',
    order_by='interaction_id'
) }}

SELECT
    interaction_id,
    langfuse_trace_id,
    session_id,
    user_id,
    if(
        nullIf(user_id, '') IS NULL,
        CAST(NULL, 'Nullable(String)'),
        lower(hex(MD5(user_id)))
    ) AS user_id_hash,
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
FROM {{ source('raw', 'user_interactions') }}
