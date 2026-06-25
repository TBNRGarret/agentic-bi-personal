{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(timestamp)',
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
    source_app_id,
    batch_id,
    ingested_at
FROM {{ source('raw', 'langfuse_generations') }}
