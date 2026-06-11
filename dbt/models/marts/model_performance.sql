SELECT
    model,
    source_owner,
    COUNT(*) AS total_calls,
    ROUND(AVG(latency_ms)::numeric, 2) AS avg_latency_ms,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p50_latency_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 2) AS p95_latency_ms,
    ROUND(AVG(total_tokens)::numeric, 0) AS avg_tokens_per_call,
    ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd,
    ROUND((SUM(cost_usd) / NULLIF(SUM(total_tokens), 0))::numeric, 6) AS cost_per_1k_tokens,
    COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error') AS error_count,
    ROUND(
        COUNT(*) FILTER (WHERE completion IS NULL OR status = 'error')::numeric
        / NULLIF(COUNT(*), 0) * 100, 2
    ) AS error_rate_pct
FROM {{ ref('stg_llm_generations') }}
GROUP BY model, source_owner
ORDER BY total_cost_usd DESC
