{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='(trace_id, span_id)'
) }}

SELECT
    trace_id,
    span_id,
    timestamp,
    service_name,
    name,
    duration_ms,
    raw_payload,
    source_app_id,
    batch_id,
    ingested_at
FROM {{ source('raw', 'signoz_traces') }}
