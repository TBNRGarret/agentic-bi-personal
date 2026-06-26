#!/usr/bin/env python3
"""
Continuous data generator for MinIO — simulates real-time streaming data from
multiple source applications into Agentic BI's raw JSONL layer.

Usage:
  # Generate data for the current hour, print summary only
  python scripts/generate_data_stream.py 

  # Generate data and persist to MinIO
  python scripts/generate_data_stream.py --interval 3600 --save
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from minio import Minio

load_dotenv()

RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:19001")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
GENERATOR_VERSION = "data-stream-v2"

SOURCE_APPS = [
    {"source_app_id": "hoang_ai_chat", "service_name": "hoang-ai-chat", "domain": "observability", "owner_team": "hoang"},
    {"source_app_id": "an_ai_chat",      "service_name": "an-ai-chat",      "domain": "finance",       "owner_team": "an"},
    {"source_app_id": "tuan_ai_chat",    "service_name": "tuan-ai-chat",    "domain": "healthcare",     "owner_team": "tuan"},
    {"source_app_id": "bach_ai_chat",    "service_name": "bach-ai-chat",    "domain": "education",     "owner_team": "bach"},
]

MODELS = [
    ("gpt-4o", Decimal("0.0000040")),
    ("gpt-4o-mini", Decimal("0.0000010")),
    ("gemini-2.0-flash", Decimal("0.0000008")),
    ("gemini-2.5-pro", Decimal("0.0000050")),
    ("claude-3-5-sonnet", Decimal("0.0000030")),
    ("claude-3-opus", Decimal("0.0000150")),
    ("llama-3.1-70b", Decimal("0.0000006")),
    ("llama-3.1-405b", Decimal("0.0000020")),
    ("deepseek-r1", Decimal("0.0000025")),
    ("deepseek-v3", Decimal("0.0000015")),
    ("mistral-large", Decimal("0.0000020")),
    ("qwen-max", Decimal("0.0000035")),
    ("command-r-plus", Decimal("0.0000025")),
    ("cohere-embed-v3", Decimal("0.0000005")),
]

# ── Prompts per source_app (domain-specific) ──
SOURCE_APP_PROMPTS: dict[str, list[str]] = {
    "hoang_ai_chat": [
        "Show P99 latency by service for the last 24h.",
        "Which services have the highest error budget burn?",
        "Compare CPU and memory usage across pods.",
        "Alert on services with >5% error rate in last hour.",
        "Show deployment frequency vs change failure rate.",
        "List top 10 slowest database queries.",
        "Show API gateway throughput by route.",
        "Compare uptime SLA across environments.",
        "Show daily LLM cost by application.",
        "Which model has the highest error rate?",
        "Compare token usage across microservices.",
        "Find slow requests in the last week.",
        "What is the average response time per model?",
        "List top 5 users by token consumption.",
        "Compare costs between yesterday and today.",
        "Identify services with >10% error rate.",
        "Detect anomalies in token usage patterns.",
        "Show peak usage hours across all services.",
        "List users with the most failed queries this month.",
        "Monitor real-time agent throughput.",
    ],
    "an_ai_chat": [
        "Show daily transaction volume and success rate.",
        "Which payment gateway has the highest failure rate?",
        "Compare revenue across product lines this quarter.",
        "Show chargeback ratio by merchant category.",
        "List top 5 customers by transaction value.",
        "Detect fraudulent transactions in the last 24h.",
        "Monthly recurring revenue (MRR) breakdown.",
        "Show currency exchange loss by settlement batch.",
        "Show conversion rate by product category.",
        "Which products have the highest cart abandonment?",
        "Compare revenue by marketing channel this week.",
        "List top 10 bestselling products by volume.",
        "Show customer retention rate by cohort.",
        "Analyze discount impact on profit margin.",
        "Show average order value by region.",
        "Detect inventory stockout risks for next week.",
        "Show hourly request distribution.",
        "Forecast next month cost based on current trends.",
        "Compare weekend vs weekday usage patterns.",
        "Calculate cost savings if we switch to cheaper models.",
    ],
    "tuan_ai_chat": [
        "Show patient wait time by department.",
        "Which diagnosis codes have the highest readmission rate?",
        "Compare medication error rates across shifts.",
        "List top 5 doctors by patient satisfaction score.",
        "Show bed occupancy rate by ward.",
        "Analyze ER throughput during peak hours.",
        "Track vaccination coverage by age group.",
        "Detect anomaly in ICU vital sign patterns.",
        "Show production line downtime by shift.",
        "Which supplier has the highest defect rate?",
        "Compare OEE across plants this month.",
        "Track on-time delivery rate by warehouse.",
        "Show inventory turnover by SKU category.",
        "Analyze shipping cost per unit by carrier.",
        "Predict maintenance window for assembly line robots.",
        "List routes with the longest average delivery time.",
        "Find sessions with failed SQL generations.",
        "Summarize failed agent queries.",
        "Show request volume by domain.",
        "Detect anomalies in token usage patterns.",
    ],
    "bach_ai_chat": [
        "Show content streaming hours by genre.",
        "Which video has the highest drop-off rate?",
        "Compare DAU/MAU ratio across game titles.",
        "List top 10 in-game purchases by revenue.",
        "Show ad impression fill rate by placement.",
        "Analyze player churn rate by level.",
        "Track content CDN bandwidth usage by region.",
        "Detect anomaly in concurrent player sessions.",
        "Show daily LLM cost by application.",
        "Which model has the highest error rate?",
        "Compare token usage across services.",
        "Find slow requests in the last week.",
        "What is the average response time per model?",
        "List top 5 users by token consumption.",
        "Compare costs between yesterday and today.",
        "Identify services with >10% error rate.",
        "Show peak usage hours across all services.",
        "List users with the most failed queries this month.",
        "Calculate cost savings if we switch to cheaper models.",
        "Show hourly request distribution.",
    ],
}

RESPONSES = {
    "success": [
        "Query completed successfully. Found {n} results in {t}ms.",
        "Analysis complete. Aggregated {n} records across {t} time windows.",
        "Report generated successfully. {n} insights identified.",
        "Data retrieved. {n} rows returned in {t}ms.",
        "Query executed. Results grouped by {dimension} with {n} entries.",
        "Trend analysis done. Detected {n} significant patterns in the data.",
        "Forecast generated. Predicted {n} units for next period.",
        "Anomaly scan complete. Flagged {n} outliers requiring investigation.",
        "Correlation analysis done. Found {n} strong relationships between variables.",
        "Segmentation complete. Grouped {n} customers into {n} distinct clusters.",
        "Funnel analysis ready. Conversion rate calculated across {n} stages.",
    ],
    "failed": [
        "Agent query failed: generated SQL referenced an unknown table '{table}'.",
        "Query timeout after {t}ms. Consider reducing the time window.",
        "Error: insufficient permissions to access requested data.",
        "LLM parsing error: could not extract SQL from the generated response.",
        "Data quality check failed: missing required fields in source data.",
        "Partition '{table}' not found in the database schema.",
        "Type mismatch: cannot compare string column with numeric value.",
        "Memory limit exceeded while processing large result set.",
        "Deadlock detected: query conflicts with another running transaction.",
        "Unsupported function called for this data type.",
        "View '{table}' depends on missing base table.",
        "Numeric overflow during aggregation calculation.",
        "Invalid date format in WHERE clause filter.",
    ],
}

TABLES = ["unknown_table", "non_existent_view", "temp_drop_me", "invalid_join", "deleted_partition", "corrupt_parquet", "missing_schema", "deprecated_view"]
DIMENSIONS = ["model", "service_name", "domain", "user_id", "hour", "region", "department", "product_category", "customer_segment", "supplier"]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def json_default(value: Any) -> str | float:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _dedup_key(source_app_id: str, batch_id: str, event_time: str, record_id: str) -> str:
    raw = f"{source_app_id}|{batch_id}|{event_time}|{record_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


_last_generated_hour: str | None = None


def _hour_partition(dt: datetime) -> str:
    return dt.strftime("year=%Y/month=%m/day=%d/hour=%H")


def raw_payload(**values: Any) -> str:
    return json.dumps(values, default=json_default, sort_keys=True)


def with_batch(
    record: dict[str, Any],
    *,
    source_app_id: str,
    batch_id: UUID,
    event_time: str,
    ingested_at: datetime,
) -> dict[str, Any]:
    record["source_app_id"] = source_app_id
    record["batch_id"] = str(batch_id)
    record["event_time"] = event_time
    record["processing_time"] = ingested_at.isoformat()
    record_id = (
        record.get("interaction_id")
        or record.get("log_id")
        or record.get("metric_id")
        or record.get("generation_id")
        or record.get("span_id")
        or str(uuid4())
    )
    record["dedup_key"] = _dedup_key(source_app_id, str(batch_id), event_time, record_id)
    return record


def hour_traffic_multiplier(hour: int) -> float:
    if 8 <= hour <= 18:
        return 1.0
    elif 19 <= hour <= 23:
        return 0.6
    else:
        return 0.3


def pick_response(status: str, duration_ms: int, **kwargs: Any) -> str:
    templates = RESPONSES.get(status, RESPONSES["success"])
    template = random.choice(templates)
    return template.format(
        n=random.randint(1, 500),
        t=duration_ms,
        dimension=random.choice(DIMENSIONS),
        table=random.choice(TABLES),
        **kwargs,
    )


# ─── Data Generation ─────────────────────────────────────────────────────────

def generate_interaction(
    app: dict[str, str],
    event_at: datetime,
    turn_index: int,
    failure_rate: float,
) -> dict[str, Any]:
    app_index = next(i for i, candidate in enumerate(SOURCE_APPS) if candidate["source_app_id"] == app["source_app_id"])
    hour = event_at.hour
    traffic_mul = hour_traffic_multiplier(hour)
    base_duration = int(500 + random.expovariate(1 / 1500) * traffic_mul)
    duration_ms = max(100, min(30000, base_duration))
    ended_at = event_at + timedelta(milliseconds=duration_ms)
    model, cost_per_token = random.choice(MODELS)
    input_tokens = int(random.gauss(180, 80))
    output_tokens = int(random.gauss(240, 120))
    input_tokens = max(10, input_tokens)
    output_tokens = max(10, output_tokens)
    total_tokens = input_tokens + output_tokens
    estimated_cost = (Decimal(total_tokens) * cost_per_token).quantize(Decimal("0.000001"))
    effective_failure = failure_rate * (1.2 if traffic_mul > 0.8 else 1.0)
    status = "failed" if random.random() < effective_failure else "success"
    session_id = f"session-{app_index + 1:02d}-{random.randint(1, 20):02d}"
    user_id = f"user-{app['owner_team']}-{random.randint(1, 10)}"
    prompts = SOURCE_APP_PROMPTS.get(app["source_app_id"], SOURCE_APP_PROMPTS["hoang_ai_chat"])
    prompt_text = random.choice(prompts)
    response_text = pick_response(status, duration_ms)
    return {
        "interaction_id": str(uuid4()),
        "langfuse_trace_id": str(uuid4()),
        "session_id": session_id,
        "user_id": user_id,
        "started_at": event_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "model": model,
        "prompt_text": prompt_text,
        "response_text": response_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": float(estimated_cost),
        "status": status,
    }


def generate_records_for_hour(
    app: dict[str, str],
    hour_start: datetime,
    batch_id: UUID,
    ingested_at: datetime,
    *,
    interactions_per_hour: int = 12,
    failure_rate: float = 0.08,
) -> dict[str, list[dict[str, Any]]]:
    source_app_id = app["source_app_id"]
    service_name = app["service_name"]
    hour = hour_start.hour
    traffic_mul = hour_traffic_multiplier(hour)
    num_records = max(1, int(interactions_per_hour * traffic_mul))
    datasets: dict[str, list[dict[str, Any]]] = {
        "user_interactions": [],
        "langfuse_generations": [],
        "signoz_logs": [],
        "signoz_traces": [],
        "signoz_metrics": [],
    }
    common_payload = {
        "domain": app["domain"],
        "environment": "dev",
        "owner_team": app["owner_team"],
        "generator_version": GENERATOR_VERSION,
    }
    for i in range(num_records):
        event_at = hour_start + timedelta(
            minutes=random.randint(0, 59),
            seconds=random.randint(0, 59),
            microseconds=random.randint(0, 999_999),
        )
        interaction = generate_interaction(app, event_at, i, failure_rate)
        trace_id = interaction["langfuse_trace_id"]
        span_id = str(uuid4())
        generation_id = str(uuid4())
        duration_ms = (datetime.fromisoformat(interaction["ended_at"]) - datetime.fromisoformat(interaction["started_at"])).total_seconds() * 1000
        status = interaction["status"]
        model = interaction["model"]

        datasets["user_interactions"].append(with_batch(
            {**interaction, "raw_payload": raw_payload(**common_payload, entity="user_interaction")},
            source_app_id=source_app_id, batch_id=batch_id, event_time=event_at.isoformat(), ingested_at=ingested_at,
        ))
        datasets["langfuse_generations"].append(with_batch({
            "generation_id": generation_id, "trace_id": trace_id,
            "timestamp": event_at.isoformat(), "model": model,
            "prompt_text": interaction["prompt_text"], "response_text": interaction["response_text"],
            "input_tokens": interaction["input_tokens"], "output_tokens": interaction["output_tokens"],
            "total_tokens": interaction["total_tokens"], "estimated_cost_usd": interaction["estimated_cost_usd"],
            "duration_ms": int(duration_ms), "status": status,
            "raw_payload": raw_payload(**common_payload, entity="langfuse_generation"),
        }, source_app_id=source_app_id, batch_id=batch_id, event_time=event_at.isoformat(), ingested_at=ingested_at))
        datasets["signoz_traces"].append(with_batch({
            "trace_id": trace_id, "span_id": span_id,
            "timestamp": event_at.isoformat(), "service_name": service_name, "name": "ai.chat.turn",
            "duration_ms": int(duration_ms),
            "raw_payload": raw_payload(**common_payload, entity="signoz_trace"),
        }, source_app_id=source_app_id, batch_id=batch_id, event_time=event_at.isoformat(), ingested_at=ingested_at))
        datasets["signoz_logs"].append(with_batch({
            "log_id": str(uuid4()),
            "timestamp": (event_at + timedelta(milliseconds=duration_ms)).isoformat(),
            "trace_id": trace_id, "span_id": span_id, "service_name": service_name,
            "severity": "INFO" if status == "success" else "WARN",
            "message": f"Chat turn {interaction['interaction_id']} completed with status={status}.",
            "raw_payload": raw_payload(**common_payload, entity="signoz_log"),
        }, source_app_id=source_app_id, batch_id=batch_id, event_time=event_at.isoformat(), ingested_at=ingested_at))
        if status == "failed":
            datasets["signoz_logs"].append(with_batch({
                "log_id": str(uuid4()),
                "timestamp": (event_at + timedelta(milliseconds=duration_ms)).isoformat(),
                "trace_id": trace_id, "span_id": span_id, "service_name": service_name,
                "severity": "ERROR", "message": interaction["response_text"],
                "raw_payload": raw_payload(**common_payload, entity="signoz_error"),
            }, source_app_id=source_app_id, batch_id=batch_id, event_time=event_at.isoformat(), ingested_at=ingested_at))
        datasets["signoz_metrics"].append(with_batch({
            "metric_id": str(uuid4()),
            "timestamp": (event_at + timedelta(milliseconds=duration_ms)).isoformat(),
            "service_name": service_name, "metric_name": "chat.turns", "metric_value": 1.0, "unit": "count",
            "attributes": json.dumps({"status": status, "model": model}, sort_keys=True),
            "raw_payload": raw_payload(**common_payload, entity="signoz_metric"),
        }, source_app_id=source_app_id, batch_id=batch_id, event_time=event_at.isoformat(), ingested_at=ingested_at))
    return datasets


# ─── MinIO I/O ───────────────────────────────────────────────────────────────

def create_minio_client() -> Minio:
    return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)


def ensure_bucket(minio_client: Minio, bucket_name: str) -> None:
    if not minio_client.bucket_exists(bucket_name):
        minio_client.make_bucket(bucket_name)


def upload_jsonl(minio_client: Minio, bucket_name: str, object_name: str, records: list[dict[str, Any]]) -> None:
    payload = ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + "\n").encode("utf-8")
    minio_client.put_object(bucket_name, object_name, BytesIO(payload), len(payload), content_type="application/jsonl")


def upload_manifest(
    minio_client: Minio,
    bucket_name: str,
    *,
    source_app_id: str,
    batch_id: UUID,
    hour_partition: str,
    generated_at: datetime,
    objects: list[dict[str, Any]],
) -> str:
    manifest = {
        "schema_version": "1.0",
        "generator_version": GENERATOR_VERSION,
        "batch_id": str(batch_id),
        "source_app_id": source_app_id,
        "generated_at": generated_at.isoformat(),
        "objects": objects,
        "row_counts": {obj["source_table"]: obj["row_count"] for obj in objects},
    }
    object_name = f"raw/{source_app_id}/_manifests/{hour_partition}/{batch_id}.json"
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    minio_client.put_object(bucket_name, object_name, BytesIO(payload), len(payload), content_type="application/json")
    return object_name


# ─── Generator Logic ─────────────────────────────────────────────────────────

def generate_one_hour(
    *,
    save: bool = False,
    interactions_per_hour: int = 12,
    failure_rate: float = 0.08,
    target_hour: datetime | None = None,
) -> dict[str, Any] | None:
    """
    Generate data for the previous completed hour (or a specific hour if target_hour is set).
    Skips if this hour partition was already generated in loop mode (idempotent per-hour).
    If save=True, upload to MinIO.
    Returns None if skipped, else summary dict.
    """
    global _last_generated_hour
    minio_client: Minio | None = None
    if save:
        minio_client = create_minio_client()
        ensure_bucket(minio_client, RAW_BUCKET)

    now = datetime.now(timezone.utc)
    if target_hour is not None:
        hour_start = target_hour.replace(tzinfo=timezone.utc) if target_hour.tzinfo is None else target_hour.astimezone(timezone.utc)
        hour_start = hour_start.replace(minute=0, second=0, microsecond=0)
    else:
        hour_start = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    hour_part = _hour_partition(hour_start)

    if target_hour is None and hour_part == _last_generated_hour:
        return None  # Already generated this hour partition (loop mode only)
    _last_generated_hour = hour_part

    hour_batch_id = uuid4()
    hour_ingested_at = now

    summary: dict[str, Any] = {
        "hour": hour_start.isoformat(),
        "batch_id": str(hour_batch_id),
        "ingested_at": hour_ingested_at.isoformat(),
        "save_to_minio": save,
        "apps": {},
        "total_rows": 0,
    }

    for app in SOURCE_APPS:
        source_app_id = app["source_app_id"]
        datasets = generate_records_for_hour(
            app, hour_start, hour_batch_id, hour_ingested_at,
            interactions_per_hour=interactions_per_hour,
            failure_rate=failure_rate,
        )

        if save:
            manifest_objects = []
            for source_table, records in datasets.items():
                if not records:
                    continue
                object_name = f"raw/{source_app_id}/{source_table}/{hour_part}/{hour_batch_id}.jsonl"
                upload_jsonl(minio_client, RAW_BUCKET, object_name, records)
                manifest_objects.append({
                    "source_table": source_table,
                    "object_name": object_name,
                    "row_count": len(records),
                    "content_type": "application/jsonl",
                })
            upload_manifest(
                minio_client, RAW_BUCKET,
                source_app_id=source_app_id,
                batch_id=hour_batch_id,
                hour_partition=hour_part,
                generated_at=hour_ingested_at,
                objects=manifest_objects,
            )

        app_total = sum(len(v) for v in datasets.values())
        summary["apps"][source_app_id] = {"tables": {k: len(v) for k, v in datasets.items()}, "total_rows": app_total}
        summary["total_rows"] += app_total

    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate streaming data into MinIO for Agentic BI.")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between each generation cycle (default: 3600 = 1h).")
    parser.add_argument("--save", action="store_true", help="Persist generated data to MinIO.")
    parser.add_argument("--hour", type=str, help="Specific hour to generate (format: YYYY-MM-DD-HH). When set, runs once and exits.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Single-run mode: generate for a specific hour and exit
    if args.hour:
        parsed_hour = datetime.strptime(args.hour, "%Y-%m-%d-%H").replace(tzinfo=timezone.utc)
        summary = generate_one_hour(save=args.save, target_hour=parsed_hour)
        label = "SAVED" if args.save else "PREVIEW"
        print(f"\n{'#'*60}")
        print(f"# Single-run @ {summary['hour']} — {label}")
        print(f"{'#'*60}")
        print(f"  Batch ID:  {summary['batch_id']}")
        print(f"  Total rows: {summary['total_rows']}")
        for app_id, info in summary["apps"].items():
            tables = ", ".join(f"{t}={c}" for t, c in info["tables"].items())
            print(f"  {app_id:20s}: {info['total_rows']:3d} rows [{tables}]")
        return

    # Loop mode: continuous generation (original behavior)
    run_number = 0
    try:
        while True:
            run_number += 1
            summary = generate_one_hour(save=args.save)
            if summary is None:
                skipped_hour = _hour_partition(
                    (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
                        minute=0, second=0, microsecond=0
                    )
                )
                print(f"[skip] {skipped_hour} already generated — waiting next cycle")
                time.sleep(args.interval)
                continue

            label = "SAVED" if args.save else "PREVIEW"
            print(f"\n{'#'*60}")
            print(f"# Run #{run_number} @ {summary['hour']} — {label}")
            print(f"{'#'*60}")
            print(f"  Batch ID:  {summary['batch_id']}")
            print(f"  Total rows: {summary['total_rows']}")
            for app_id, info in summary["apps"].items():
                tables = ", ".join(f"{t}={c}" for t, c in info["tables"].items())
                print(f"  {app_id:20s}: {info['total_rows']:3d} rows [{tables}]")
            print(f"\nSleeping {args.interval}s...")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {run_number} run(s).")
        sys.exit(0)


if __name__ == "__main__":
    main()
