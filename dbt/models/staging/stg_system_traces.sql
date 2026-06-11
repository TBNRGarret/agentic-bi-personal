SELECT
    id,
    trace_id,
    span_id,
    parent_span_id,
    service_name,
    operation_name,
    span_kind,
    status_code,
    status_message,
    duration_ns::bigint AS duration_ns,
    duration_ns::numeric / 1e6 AS duration_ms,
    start_time::timestamptz AS start_time,
    end_time::timestamptz AS end_time,
    attributes,
    resource_attrs,
    source_owner,
    _batch_id,
    _ingested_at
FROM {{ source('raw', 'system_traces') }}
WHERE id IS NOT NULL
