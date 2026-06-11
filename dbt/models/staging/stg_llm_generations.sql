SELECT
    id,
    langfuse_id,
    trace_id,
    generation_name,
    model,
    model_parameters,
    prompt,
    completion,
    NULLIF(prompt_tokens, 'None')::bigint AS prompt_tokens,
    NULLIF(completion_tokens, 'None')::bigint AS completion_tokens,
    NULLIF(total_tokens, 'None')::bigint AS total_tokens,
    NULLIF(cost_usd, 'None')::numeric(12,6) AS cost_usd,
    NULLIF(latency_ms, 'None')::numeric(12,2) AS latency_ms,
    status,
    NULLIF(start_time, 'None')::timestamptz AS start_time,
    NULLIF(end_time, 'None')::timestamptz AS end_time,
    metadata,
    source_owner,
    _batch_id,
    _ingested_at
FROM {{ source('raw', 'llm_generations') }}
WHERE id IS NOT NULL
