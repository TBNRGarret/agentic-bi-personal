{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='interaction_id'
) }}

SELECT
    interaction_id,
    langfuse_trace_id,
    session_id,
    user_id,
    started_at,
    ended_at,
    model,
    prompt_text,
    response_text,
    input_tokens,
    output_tokens,
    total_tokens,
    estimated_cost_usd,
    status,
    raw_payload,
    ingested_at
FROM {{ source('raw', 'user_interactions') }}
