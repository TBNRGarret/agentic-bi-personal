{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='generation_id'
) }}

SELECT
    generation_id,
    trace_id,
    timestamp,
    model,
    prompt_text,
    response_text,
    input_tokens,
    output_tokens,
    total_tokens,
    estimated_cost_usd,
    duration_ms,
    status,
    raw_payload,
    ingested_at
FROM {{ source('raw', 'langfuse_generations') }}
