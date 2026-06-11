SELECT
    metric_id,
    timestamp::timestamptz AS timestamp,
    service_name,
    metric_name,
    metric_value::numeric(12,4) AS metric_value,
    unit,
    attributes,
    raw_payload,
    source_owner,
    _batch_id,
    _ingested_at
FROM {{ source('raw', 'system_metrics') }}
WHERE metric_id IS NOT NULL
