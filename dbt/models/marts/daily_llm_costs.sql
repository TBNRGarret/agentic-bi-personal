SELECT
    DATE(start_time) AS date,
    source_owner,
    model,
    COUNT(*) AS total_requests,
    COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error') AS error_count,
    ROUND(
        COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error')::numeric
        / NULLIF(COUNT(*), 0) * 100, 2
    ) AS error_rate_pct,
    SUM(prompt_tokens) AS total_prompt_tokens,
    SUM(completion_tokens) AS total_completion_tokens,
    SUM(total_tokens) AS total_tokens,
    ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd,
    ROUND(AVG(latency_ms)::numeric, 2) AS avg_latency_ms,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p50_latency_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p95_latency_ms
FROM {{ ref('stg_llm_generations') }}
GROUP BY DATE(start_time), source_owner, model
ORDER BY date DESC, total_cost_usd DESC
