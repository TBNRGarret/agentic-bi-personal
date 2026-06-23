SELECT
    source_owner,
    DATE(MIN(start_time)) AS first_activity,
    DATE(MAX(start_time)) AS last_activity,
    COUNT(DISTINCT model) AS unique_models_used,
    COUNT(*) AS total_requests,
    SUM(total_tokens) AS total_tokens,
    ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd
FROM {{ ref('stg_llm_generations') }}
GROUP BY source_owner
ORDER BY total_cost_usd DESC
