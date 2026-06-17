#!/usr/bin/env python3
"""Seed deterministic Sprint 1 raw JSONL batches into MinIO.

This is the canonical local source layer for Sprint 1:
    MinIO JSONL + manifest -> Airflow -> ClickHouse raw/staging/mart.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from minio import Minio


RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "47.131.228.222:19001")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
SEED_VERSION = "sprint1-minio-v2"

SOURCE_APPS = [
    {"source_app_id": "hoang_ai_chat", "service_name": "hoang-ai-chat", "domain": "observability", "owner_team": "hoang"},
    {"source_app_id": "an_ai_chat", "service_name": "an-ai-chat", "domain": "finance", "owner_team": "an"},
    {"source_app_id": "tuan_ai_chat", "service_name": "tuan-ai-chat", "domain": "healthcare", "owner_team": "tuan"},
    {"source_app_id": "bach_ai_chat", "service_name": "bach-ai-chat", "domain": "education", "owner_team": "bach"},
]

MODELS = [
    ("gpt-4o", Decimal("0.0000040")),
    ("gpt-4o-mini", Decimal("0.0000010")),
    ("gemini-2.0-flash", Decimal("0.0000008")),
    ("claude-3-5-sonnet", Decimal("0.0000030")),
    ("llama-3.1-70b", Decimal("0.0000006")),
]

PROMPTS = [
    "Show daily LLM cost by application.",
    "Which model has the highest error rate?",
    "Compare token usage across services.",
    "Find slow requests in the last week.",
    "Summarize failed agent queries.",
    "Show request volume by domain.",
]


def parse_base_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_default(value: Any) -> str | float:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def raw_payload(**values: Any) -> str:
    return json.dumps(values, default=json_default, sort_keys=True)


def with_batch(record: dict[str, Any], *, source_app_id: str, batch_id: UUID, ingested_at: datetime) -> dict[str, Any]:
    record["source_app_id"] = source_app_id
    record["batch_id"] = str(batch_id)
    record["ingested_at"] = ingested_at.isoformat()
    return record


def build_app_records(app: dict[str, str], *, batch_id: UUID, base_time: datetime) -> dict[str, list[dict[str, Any]]]:
    source_app_id = app["source_app_id"]
    service_name = app["service_name"]
    app_index = next(i for i, candidate in enumerate(SOURCE_APPS) if candidate["source_app_id"] == source_app_id)
    datasets: dict[str, list[dict[str, Any]]] = {
        "user_interactions": [],
        "langfuse_generations": [],
        "signoz_logs": [],
        "signoz_traces": [],
        "signoz_metrics": [],
    }

    for day_offset in range(14):
        for turn_index in range(3):
            event_at = base_time - timedelta(days=day_offset, hours=app_index + turn_index * 3)
            duration_ms = 800 + ((day_offset + 1) * (turn_index + 2) * 137) % 18000
            ended_at = event_at + timedelta(milliseconds=duration_ms)
            model, cost_per_token = MODELS[(day_offset + turn_index + app_index) % len(MODELS)]
            input_tokens = 48 + ((day_offset * 11 + turn_index * 17 + app_index * 5) % 220)
            output_tokens = 72 + ((day_offset * 13 + turn_index * 19 + app_index * 7) % 260)
            total_tokens = input_tokens + output_tokens
            estimated_cost = (Decimal(total_tokens) * cost_per_token).quantize(Decimal("0.000001"))
            status = "failed" if (day_offset + turn_index + app_index) % 8 == 0 else "success"

            stable_suffix = f"{app_index + 1:02d}-{day_offset:02d}-{turn_index:02d}"
            trace_id = f"trace-{stable_suffix}"
            span_id = f"span-{stable_suffix}"
            interaction_id = f"int-{stable_suffix}"
            generation_id = f"gen-{stable_suffix}"
            session_id = f"session-{app_index + 1:02d}-{day_offset % 5:02d}"
            user_id = f"user-{app['owner_team']}-{turn_index + 1}"
            prompt_text = PROMPTS[(day_offset + turn_index + app_index) % len(PROMPTS)]
            response_text = (
                "Query completed successfully."
                if status == "success"
                else "Agent query failed because the generated SQL referenced an unknown table."
            )
            common_payload = {
                "domain": app["domain"],
                "environment": "dev",
                "owner_team": app["owner_team"],
                "seed_version": SEED_VERSION,
            }

            datasets["user_interactions"].append(with_batch({
                "interaction_id": interaction_id,
                "langfuse_trace_id": trace_id,
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
                "raw_payload": raw_payload(**common_payload, entity="user_interaction"),
            }, source_app_id=source_app_id, batch_id=batch_id, ingested_at=base_time))

            datasets["langfuse_generations"].append(with_batch({
                "generation_id": generation_id,
                "trace_id": trace_id,
                "timestamp": event_at.isoformat(),
                "model": model,
                "prompt_text": prompt_text,
                "response_text": response_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": float(estimated_cost),
                "duration_ms": duration_ms,
                "status": status,
                "raw_payload": raw_payload(**common_payload, entity="langfuse_generation"),
            }, source_app_id=source_app_id, batch_id=batch_id, ingested_at=base_time))

            datasets["signoz_traces"].append(with_batch({
                "trace_id": trace_id,
                "span_id": span_id,
                "timestamp": event_at.isoformat(),
                "service_name": service_name,
                "name": "ai.chat.turn",
                "duration_ms": duration_ms,
                "raw_payload": raw_payload(**common_payload, entity="signoz_trace"),
            }, source_app_id=source_app_id, batch_id=batch_id, ingested_at=base_time))

            datasets["signoz_logs"].append(with_batch({
                "log_id": f"log-info-{stable_suffix}",
                "timestamp": ended_at.isoformat(),
                "trace_id": trace_id,
                "span_id": span_id,
                "service_name": service_name,
                "severity": "INFO" if status == "success" else "WARN",
                "message": f"Chat turn {interaction_id} completed with status={status}.",
                "raw_payload": raw_payload(**common_payload, entity="signoz_log"),
            }, source_app_id=source_app_id, batch_id=batch_id, ingested_at=base_time))
            if status == "failed":
                datasets["signoz_logs"].append(with_batch({
                    "log_id": f"log-error-{stable_suffix}",
                    "timestamp": ended_at.isoformat(),
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "service_name": service_name,
                    "severity": "ERROR",
                    "message": "DB::Exception: Unknown table referenced by generated SQL",
                    "raw_payload": raw_payload(**common_payload, entity="signoz_error"),
                }, source_app_id=source_app_id, batch_id=batch_id, ingested_at=base_time))

            datasets["signoz_metrics"].append(with_batch({
                "metric_id": f"metric-{stable_suffix}",
                "timestamp": ended_at.isoformat(),
                "service_name": service_name,
                "metric_name": "chat.turns",
                "metric_value": 1.0,
                "unit": "count",
                "attributes": json.dumps({"status": status, "model": model}, sort_keys=True),
                "raw_payload": raw_payload(**common_payload, entity="signoz_metric"),
            }, source_app_id=source_app_id, batch_id=batch_id, ingested_at=base_time))

    return datasets


def create_minio_client() -> Minio:
    return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)


def ensure_bucket(minio_client: Minio, bucket_name: str) -> None:
    if not minio_client.bucket_exists(bucket_name):
        minio_client.make_bucket(bucket_name)


def upload_jsonl(minio_client: Minio, bucket_name: str, object_name: str, records: list[dict[str, Any]]) -> None:
    payload = ("\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n").encode("utf-8")
    minio_client.put_object(bucket_name, object_name, BytesIO(payload), len(payload), content_type="application/jsonl")


def upload_manifest(
    minio_client: Minio,
    bucket_name: str,
    *,
    source_app_id: str,
    batch_id: UUID,
    generated_at: datetime,
    objects: list[dict[str, Any]],
) -> str:
    manifest = {
        "schema_version": "1.0",
        "seed_version": SEED_VERSION,
        "batch_id": str(batch_id),
        "source_app_id": source_app_id,
        "generated_at": generated_at.isoformat(),
        "objects": objects,
        "row_counts": {obj["source_table"]: obj["row_count"] for obj in objects},
    }
    object_name = f"raw/{source_app_id}/_manifests/{batch_id}.json"
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    minio_client.put_object(bucket_name, object_name, BytesIO(payload), len(payload), content_type="application/json")
    return object_name


def seed_minio(
    *,
    minio_client: Minio | None = None,
    bucket_name: str = RAW_BUCKET,
    batch_id: UUID | None = None,
    base_time: datetime | None = None,
    source_app_ids: set[str] | None = None,
) -> dict[str, Any]:
    minio_client = minio_client or create_minio_client()
    batch_id = batch_id or uuid5(NAMESPACE_URL, f"agentic-bi:{SEED_VERSION}")
    base_time = base_time or parse_base_time(None)
    selected_apps = [app for app in SOURCE_APPS if source_app_ids is None or app["source_app_id"] in source_app_ids]
    if not selected_apps:
        raise ValueError("No source apps selected for MinIO seed")

    ensure_bucket(minio_client, bucket_name)
    summary: dict[str, Any] = {"bucket": bucket_name, "batch_id": str(batch_id), "generated_at": base_time.isoformat(), "source_apps": {}}
    for app in selected_apps:
        source_app_id = app["source_app_id"]
        datasets = build_app_records(app, batch_id=batch_id, base_time=base_time)
        manifest_objects = []
        for source_table, records in datasets.items():
            object_name = f"raw/{source_app_id}/{source_table}/{batch_id}.jsonl"
            upload_jsonl(minio_client, bucket_name, object_name, records)
            manifest_objects.append({
                "source_table": source_table,
                "object_name": object_name,
                "row_count": len(records),
                "content_type": "application/jsonl",
            })
        manifest_name = upload_manifest(
            minio_client,
            bucket_name,
            source_app_id=source_app_id,
            batch_id=batch_id,
            generated_at=base_time,
            objects=manifest_objects,
        )
        summary["source_apps"][source_app_id] = {
            "manifest": manifest_name,
            "tables": {
                item["source_table"]: {"object_name": item["object_name"], "row_count": item["row_count"]}
                for item in manifest_objects
            },
        }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed deterministic Agentic BI raw batches into MinIO.")
    parser.add_argument("--bucket", default=RAW_BUCKET, help="Target MinIO bucket.")
    parser.add_argument("--base-time", default=os.getenv("SEED_BASE_TIME"), help="ISO timestamp for latest seeded data.")
    parser.add_argument("--batch-id", default=os.getenv("SEED_BATCH_ID"), help="Explicit UUID batch id.")
    parser.add_argument("--new-batch", action="store_true", help="Use a random batch id instead of the deterministic demo id.")
    parser.add_argument(
        "--source-app",
        action="append",
        choices=[app["source_app_id"] for app in SOURCE_APPS],
        help="Limit seeding to one source app. Repeat for multiple apps.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.new_batch:
        batch_id = uuid4()
    elif args.batch_id:
        batch_id = UUID(args.batch_id)
    else:
        batch_id = uuid5(NAMESPACE_URL, f"agentic-bi:{SEED_VERSION}")

    summary = seed_minio(
        bucket_name=args.bucket,
        batch_id=batch_id,
        base_time=parse_base_time(args.base_time),
        source_app_ids=set(args.source_app) if args.source_app else None,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
