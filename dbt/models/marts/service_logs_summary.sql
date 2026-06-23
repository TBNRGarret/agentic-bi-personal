SELECT
    service_name,
    source_owner,
    DATE(timestamp::timestamptz) AS date,
    COUNT(*) AS total_logs,
    COUNT(*) FILTER (WHERE severity_text = 'ERROR' OR severity_text = 'FATAL') AS error_logs,
    COUNT(DISTINCT trace_id) AS distinct_traces
FROM {{ source('raw', 'system_logs') }}
GROUP BY service_name, source_owner, DATE(timestamp::timestamptz)
ORDER BY date DESC, total_logs DESC
