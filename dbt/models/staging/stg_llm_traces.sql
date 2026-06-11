SELECT
    id,
    langfuse_id,
    trace_name,
    user_id,
    session_id,
    input,
    output,
    metadata,
    tags,
    start_time::timestamptz AS start_time,
    end_time::timestamptz AS end_time,
    latency_ms::numeric(12,2) AS latency_ms,
    total_cost_usd::numeric(12,6) AS total_cost_usd,
    source_owner,
    _batch_id,
    _ingested_at
FROM {{ source('raw', 'llm_traces') }}
WHERE id IS NOT NULL
