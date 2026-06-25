{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    partition_by='toYYYYMM(timestamp)',
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
    source_app_id,
    batch_id,
    ingested_at
FROM {{ source('raw', 'signoz_logs') }}
