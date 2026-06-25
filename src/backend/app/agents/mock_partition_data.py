"""Mock data generator for partitioned observability/LLM/ingestion tables.

Generates ~90 days of realistic data with proper partition distributions
for testing partition-aware agent features without a live ClickHouse.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any


_DEFAULT_SERVICES = ["api-gateway", "auth-service", "ml-inference", "data-pipeline"]
_DEFAULT_SOURCES = ["agentic-bi", "monitoring-v2", "data-ingestion"]
_DEFAULT_MODELS = ["openai/gpt-4o", "openai/gpt-oss-120b", "anthropic/claude-3-sonnet", "mistral/mistral-large"]


def generate_partitioned_observability_data(
    days: int = 90,
    services: list[str] | None = None,
    sources: list[str] | None = None,
    models: list[str] | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Generate mock Gold/Silver data with realistic partition distributions.

    Args:
        days: Number of days of historical data to generate.
        services: Service names for observability data.
        sources: Source application names.
        models: LLM model names.
        seed: Random seed for reproducibility.

    Returns:
        Dictionary with table data and partition metadata:
        {
            "gold.gold_system_observability_daily": [...],
            "gold.gold_llm_usage_daily": [...],
            "gold.gold_ingestion_health_daily": [...],
            "gold.gold_trace_health_daily": [...],
            "silver.silver_system_traces": [...],
            "partition_metadata": { ... },
        }
    """
    rng = random.Random(seed)
    services = services or _DEFAULT_SERVICES
    sources = sources or _DEFAULT_SOURCES
    models = models or _DEFAULT_MODELS
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    result: dict[str, Any] = {
        "gold.gold_system_observability_daily": _generate_observability(
            start_date, end_date, services, sources, rng
        ),
        "gold.gold_llm_usage_daily": _generate_llm_usage(
            start_date, end_date, models, sources, rng
        ),
        "gold.gold_ingestion_health_daily": _generate_ingestion(
            start_date, end_date, sources, rng
        ),
        "gold.gold_trace_health_daily": _generate_trace_health(
            start_date, end_date, sources, rng
        ),
        "silver.silver_system_traces": _generate_silver_traces(
            start_date, end_date, services, sources, rng, sample_days=7
        ),
    }

    # Build partition metadata
    result["partition_metadata"] = _build_partition_metadata(result, start_date, end_date)

    return result


def _generate_observability(
    start: date,
    end: date,
    services: list[str],
    sources: list[str],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate gold.gold_system_observability_daily rows."""
    rows: list[dict[str, Any]] = []
    current = start
    while current <= end:
        is_weekend = current.weekday() >= 5
        base_traffic = 0.4 if is_weekend else 1.0

        for service in services:
            for source in sources:
                # Service-specific baseline latency
                base_latency = {
                    "api-gateway": 85.0,
                    "auth-service": 45.0,
                    "ml-inference": 350.0,
                    "data-pipeline": 200.0,
                }.get(service, 100.0)

                span_count = int(rng.gauss(1000, 300) * base_traffic)
                span_count = max(10, span_count)
                error_rate = max(0, rng.gauss(0.02, 0.01))

                # Inject occasional spikes
                if rng.random() < 0.03:
                    error_rate = rng.uniform(0.05, 0.2)
                if rng.random() < 0.02:
                    base_latency *= rng.uniform(2.0, 5.0)

                error_span_count = int(span_count * error_rate)
                avg_latency = round(base_latency * rng.uniform(0.7, 1.5), 2)
                p95_latency = round(avg_latency * rng.uniform(2.0, 4.0), 2)

                rows.append({
                    "metric_date": current.isoformat(),
                    "service_name": service,
                    "source_app": source,
                    "source_app_id": source,
                    "span_count": span_count,
                    "error_span_count": error_span_count,
                    "avg_span_duration_ms": avg_latency,
                    "p95_span_duration_ms": p95_latency,
                    "avg_latency_ms": avg_latency,
                    "p95_latency_ms": p95_latency,
                })
        current += timedelta(days=1)
    return rows


def _generate_llm_usage(
    start: date,
    end: date,
    models: list[str],
    sources: list[str],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate gold.gold_llm_usage_daily rows."""
    rows: list[dict[str, Any]] = []
    current = start

    # Model-specific cost rates (per 1K tokens)
    cost_rates = {
        "openai/gpt-4o": 0.015,
        "openai/gpt-oss-120b": 0.0,
        "anthropic/claude-3-sonnet": 0.012,
        "mistral/mistral-large": 0.008,
    }

    while current <= end:
        is_weekend = current.weekday() >= 5
        base_traffic = 0.3 if is_weekend else 1.0

        for model in models:
            for source in sources:
                gen_count = int(rng.gauss(50, 20) * base_traffic)
                gen_count = max(1, gen_count)

                tokens_per_gen = rng.gauss(800, 300)
                total_tokens = int(gen_count * max(100, tokens_per_gen))

                cost_rate = cost_rates.get(model, 0.01)
                total_cost = round(total_tokens / 1000 * cost_rate, 6)

                error_count = int(gen_count * rng.uniform(0, 0.05))
                success_count = gen_count - error_count

                avg_latency = round(rng.gauss(800, 300), 2)
                p95_latency = round(avg_latency * rng.uniform(2.0, 3.5), 2)

                rows.append({
                    "metric_date": current.isoformat(),
                    "model": model,
                    "source_app": source,
                    "source_app_id": source,
                    "generation_count": gen_count,
                    "success_count": success_count,
                    "error_count": error_count,
                    "total_tokens": total_tokens,
                    "prompt_tokens": int(total_tokens * 0.6),
                    "completion_tokens": int(total_tokens * 0.4),
                    "total_cost_usd": total_cost,
                    "avg_latency_ms": max(50.0, avg_latency),
                    "p95_latency_ms": max(100.0, p95_latency),
                })
        current += timedelta(days=1)
    return rows


def _generate_ingestion(
    start: date,
    end: date,
    sources: list[str],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate gold.gold_ingestion_health_daily rows."""
    rows: list[dict[str, Any]] = []
    current = start
    while current <= end:
        for source in sources:
            batch_count = int(rng.gauss(24, 6))
            batch_count = max(1, batch_count)

            failed_rate = max(0, rng.gauss(0.02, 0.02))
            if rng.random() < 0.04:  # 4% chance of ingestion incident
                failed_rate = rng.uniform(0.1, 0.4)

            failed_batch_count = int(batch_count * failed_rate)
            quarantined = int(batch_count * rng.uniform(0, 0.05))
            loaded_rows = int(rng.gauss(50000, 20000))
            loaded_rows = max(100, loaded_rows)

            rows.append({
                "metric_date": current.isoformat(),
                "source_app": source,
                "batch_count": batch_count,
                "failed_batch_count": failed_batch_count,
                "quarantined_batch_count": quarantined,
                "loaded_row_count": loaded_rows,
                "loaded_batch_count": batch_count - failed_batch_count - quarantined,
            })
        current += timedelta(days=1)
    return rows


def _generate_trace_health(
    start: date,
    end: date,
    sources: list[str],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate gold.gold_trace_health_daily rows."""
    rows: list[dict[str, Any]] = []
    current = start
    while current <= end:
        is_weekend = current.weekday() >= 5
        base_traffic = 0.5 if is_weekend else 1.0

        for source in sources:
            trace_count = int(rng.gauss(2000, 600) * base_traffic)
            trace_count = max(50, trace_count)
            error_rate = max(0, rng.gauss(0.03, 0.02))
            error_count = int(trace_count * error_rate)
            success_count = trace_count - error_count

            avg_latency = round(rng.gauss(150, 50), 2)
            p95_latency = round(avg_latency * rng.uniform(2.5, 4.0), 2)

            rows.append({
                "metric_date": current.isoformat(),
                "source_app": source,
                "trace_count": trace_count,
                "success_count": success_count,
                "error_count": error_count,
                "avg_latency_ms": max(10.0, avg_latency),
                "p95_latency_ms": max(20.0, p95_latency),
            })
        current += timedelta(days=1)
    return rows


def _generate_silver_traces(
    start: date,
    end: date,
    services: list[str],
    sources: list[str],
    rng: random.Random,
    sample_days: int = 7,
) -> list[dict[str, Any]]:
    """Generate silver.silver_system_traces rows (sampled, daily partitioned)."""
    rows: list[dict[str, Any]] = []
    # Only generate for the last N days to keep data volume manageable
    sample_start = max(start, end - timedelta(days=sample_days))
    current = sample_start

    statuses = ["OK", "OK", "OK", "OK", "ERROR"]  # 20% error rate
    while current <= end:
        traces_per_day = rng.randint(30, 80)
        for _ in range(traces_per_day):
            hour = rng.randint(0, 23)
            minute = rng.randint(0, 59)
            second = rng.randint(0, 59)
            event_time = f"{current.isoformat()} {hour:02d}:{minute:02d}:{second:02d}"

            service = rng.choice(services)
            source = rng.choice(sources)
            duration = max(1.0, rng.gauss(200, 100))

            rows.append({
                "event_time": event_time,
                "source_app": source,
                "service_name": service,
                "duration_ms": round(duration, 2),
                "span_status": rng.choice(statuses),
            })
        current += timedelta(days=1)
    return rows


def _build_partition_metadata(
    data: dict[str, Any],
    start: date,
    end: date,
) -> dict[str, Any]:
    """Build partition metadata summary for all tables."""
    metadata: dict[str, Any] = {}

    for table_name, rows in data.items():
        if table_name == "partition_metadata":
            continue
        if not rows:
            continue

        if table_name.startswith("gold."):
            # Monthly partitions for Gold tables
            partition_key = "toYYYYMM(metric_date)"
            partitions = _count_monthly_partitions(rows, "metric_date")
        elif table_name.startswith("silver."):
            # Daily partitions for Silver tables
            time_col = "event_time" if "event_time" in rows[0] else "metric_date"
            partition_key = f"toYYYYMMDD({time_col})"
            partitions = _count_daily_partitions(rows, time_col)
        else:
            continue

        metadata[table_name] = {
            "partition_key": partition_key,
            "total_rows": len(rows),
            "partitions": partitions,
        }

    return metadata


def _count_monthly_partitions(
    rows: list[dict[str, Any]], date_column: str
) -> list[dict[str, Any]]:
    """Count rows per monthly partition."""
    counts: dict[str, int] = {}
    for row in rows:
        date_val = str(row.get(date_column) or "")[:7].replace("-", "")
        if len(date_val) >= 6:
            partition_id = date_val[:6]
            counts[partition_id] = counts.get(partition_id, 0) + 1
    return [
        {"id": pid, "rows": count, "size_bytes": count * 125}
        for pid, count in sorted(counts.items())
    ]


def _count_daily_partitions(
    rows: list[dict[str, Any]], date_column: str
) -> list[dict[str, Any]]:
    """Count rows per daily partition."""
    counts: dict[str, int] = {}
    for row in rows:
        date_val = str(row.get(date_column) or "")[:10].replace("-", "")
        if len(date_val) >= 8:
            partition_id = date_val[:8]
            counts[partition_id] = counts.get(partition_id, 0) + 1
    return [
        {"id": pid, "rows": count, "size_bytes": count * 125}
        for pid, count in sorted(counts.items())
    ]
