SELECT
    id,
    langfuse_id,
    trace_id,
    generation_name,
    model,
    model_parameters,
    prompt,
    completion,
    prompt_tokens::bigint AS prompt_tokens,
    completion_tokens::bigint AS completion_tokens,
    total_tokens::bigint AS total_tokens,
    cost_usd::numeric(12,6) AS cost_usd,
    latency_ms::numeric(12,2) AS latency_ms,
    status,
    start_time::timestamptz AS start_time,
    end_time::timestamptz AS end_time,
    metadata,
    source_owner,
    _batch_id,
    _ingested_at
FROM {{ source('raw', 'llm_generations') }}
WHERE id IS NOT NULL
