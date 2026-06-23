{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='metric_id'
) }}

SELECT
    metric_id,
    timestamp,
    service_name,
    metric_name,
    metric_value,
    unit,
    attributes,
    raw_payload,
    source_app_id,
    batch_id,
    ingested_at
FROM {{ source('raw', 'signoz_metrics') }}
