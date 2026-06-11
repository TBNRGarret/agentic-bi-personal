SELECT
    id,
    timestamp::timestamptz AS timestamp,
    severity_text,
    severity_number::integer AS severity_number,
    body,
    service_name,
    trace_id,
    span_id,
    attributes,
    resource_attrs,
    source_owner,
    _batch_id,
    _ingested_at
FROM {{ source('raw', 'system_logs') }}
WHERE id IS NOT NULL
