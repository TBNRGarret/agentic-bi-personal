SELECT
    service_name,
    source_owner,
    COUNT(DISTINCT trace_id) AS total_traces,
    COUNT(*) AS total_spans,
    COUNT(*) FILTER (WHERE status_code = 'Error' OR status_code = '') AS error_spans,
    ROUND(
        COUNT(*) FILTER (WHERE status_code = 'Error' OR status_code = '')::numeric
        / NULLIF(COUNT(*), 0) * 100, 2
    ) AS error_rate_pct,
    ROUND(AVG(duration_ms)::numeric, 2) AS avg_duration_ms,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms)::numeric, 2) AS p50_duration_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 2) AS p95_duration_ms,
    COUNT(DISTINCT operation_name) AS unique_operations
FROM {{ ref('stg_system_traces') }}
GROUP BY service_name, source_owner
ORDER BY total_spans DESC
