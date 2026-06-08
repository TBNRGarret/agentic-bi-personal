{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='log_id'
) }}

SELECT
    log_id,
    timestamp,
    trace_id,
    span_id,
    service_name,
    severity,
    message,
    raw_payload,
    ingested_at
FROM {{ source('raw', 'signoz_logs') }}
