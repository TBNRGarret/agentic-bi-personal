#!/usr/bin/env python3
"""Seed mock telemetry JSONL files directly into MinIO for the ClickHouse pipeline.

Bypasses the legacy Postgres DWH — writes directly to the MinIO landing zone
in the format expected by the agentic_bi_pipeline DAG.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

from minio import Minio

RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
SOURCE_APP_ID = os.getenv("SOURCE_APP_ID", "hoangvl_ai_chat")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:19001")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"


def main():
    mc = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
               secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
    if not mc.bucket_exists(RAW_BUCKET):
        mc.make_bucket(RAW_BUCKET)
        print(f"Created bucket: {RAW_BUCKET}")

    now = datetime.now(timezone.utc)
    batch_id = str(uuid4())

    # ── user_interactions ──────────────────────────────────────
    interactions = [
        {
            "interaction_id": "int-1001",
            "langfuse_trace_id": "trace-success-123",
            "session_id": "session-abc",
            "user_id": "user-hoangvl",
            "started_at": (now - timedelta(minutes=5)).isoformat(),
            "ended_at": (now - timedelta(minutes=4, seconds=50)).isoformat(),
            "model": "gpt-4o",
            "prompt_text": "Hello, what is ClickHouse?",
            "response_text": "ClickHouse is a fast open-source column-oriented DBMS for OLAP.",
            "input_tokens": 15,
            "output_tokens": 25,
            "total_tokens": 40,
            "estimated_cost_usd": 0.00020,
            "status": "success",
            "raw_payload": json.dumps({"metadata": "success_chat"}),
        },
        {
            "interaction_id": "int-1002",
            "langfuse_trace_id": "trace-error-456",
            "session_id": "session-abc",
            "user_id": "user-hoangvl",
            "started_at": (now - timedelta(minutes=2)).isoformat(),
            "ended_at": (now - timedelta(minutes=1, seconds=55)).isoformat(),
            "model": "gpt-4o",
            "prompt_text": "Give me the total sales by country",
            "response_text": "Internal server error occurred while executing the query.",
            "input_tokens": 10,
            "output_tokens": 15,
            "total_tokens": 25,
            "estimated_cost_usd": 0.00012,
            "status": "failed",
            "raw_payload": json.dumps({"metadata": "failed_chat"}),
        },
        {
            "interaction_id": "int-1003",
            "langfuse_trace_id": "trace-success-789",
            "session_id": "session-xyz",
            "user_id": "user-anlq",
            "started_at": (now - timedelta(hours=1)).isoformat(),
            "ended_at": (now - timedelta(minutes=59)).isoformat(),
            "model": "gemini-2.0-flash",
            "prompt_text": "List all databases in system",
            "response_text": "The system has 3 databases: vsf_raw, vsf_stg, and vsf_mart.",
            "input_tokens": 8,
            "output_tokens": 12,
            "total_tokens": 20,
            "estimated_cost_usd": 0.00005,
            "status": "success",
            "raw_payload": json.dumps({"metadata": "success_gemini"}),
        },
    ]

    # ── langfuse_generations ──────────────────────────────────
    generations = [
        {
            "generation_id": "gen-2001",
            "trace_id": "trace-success-123",
            "timestamp": (now - timedelta(minutes=4, seconds=58)).isoformat(),
            "model": "gpt-4o",
            "prompt_text": "Hello, what is ClickHouse?",
            "response_text": "ClickHouse is a fast open-source column-oriented DBMS for OLAP.",
            "input_tokens": 15,
            "output_tokens": 25,
            "total_tokens": 40,
            "estimated_cost_usd": 0.00020,
            "duration_ms": 10000,
            "status": "success",
            "raw_payload": json.dumps({"metadata": "gen_success_detail"}),
        },
        {
            "generation_id": "gen-2002",
            "trace_id": "trace-error-456",
            "timestamp": (now - timedelta(minutes=1, seconds=58)).isoformat(),
            "model": "gpt-4o",
            "prompt_text": "Give me the total sales by country",
            "response_text": "Internal server error occurred while executing the query.",
            "input_tokens": 10,
            "output_tokens": 15,
            "total_tokens": 25,
            "estimated_cost_usd": 0.00012,
            "duration_ms": 5000,
            "status": "failed",
            "raw_payload": json.dumps({"metadata": "gen_failed_detail"}),
        },
        {
            "generation_id": "gen-2003",
            "trace_id": "trace-success-789",
            "timestamp": (now - timedelta(hours=1, minutes=1)).isoformat(),
            "model": "gemini-2.0-flash",
            "prompt_text": "List all databases in system",
            "response_text": "The system has 3 databases: vsf_raw, vsf_stg, and vsf_mart.",
            "input_tokens": 8,
            "output_tokens": 12,
            "total_tokens": 20,
            "estimated_cost_usd": 0.00005,
            "duration_ms": 60000,
            "status": "success",
            "raw_payload": json.dumps({"metadata": "gen_gemini_detail"}),
        },
    ]

    # ── signoz_logs ───────────────────────────────────────────
    logs = [
        {
            "log_id": "log-3001",
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "trace_id": "trace-success-123",
            "span_id": "span-123",
            "service_name": "hoangvl-ai-chat",
            "severity": "INFO",
            "message": "Chat turn completed successfully.",
            "raw_payload": json.dumps({"level": "info", "component": "app"}),
        },
        {
            "log_id": "log-3002",
            "timestamp": (now - timedelta(minutes=1, seconds=57)).isoformat(),
            "trace_id": "trace-error-456",
            "span_id": "span-456",
            "service_name": "hoangvl-ai-chat",
            "severity": "ERROR",
            "message": "DB::Exception: Table sales_data does not exist in database default",
            "raw_payload": json.dumps({"level": "error", "component": "database", "error": "TableNotFound"}),
        },
        {
            "log_id": "log-3003",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            "trace_id": "trace-success-789",
            "span_id": "span-789",
            "service_name": "anlq-ai-chat",
            "severity": "INFO",
            "message": "Gemini request processed",
            "raw_payload": json.dumps({"level": "info"}),
        },
    ]

    # ── signoz_traces ─────────────────────────────────────────
    traces = [
        {
            "trace_id": "trace-success-123",
            "span_id": "span-123",
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "service_name": "hoangvl-ai-chat",
            "name": "ai.chat.turn",
            "duration_ms": 10000,
            "raw_payload": json.dumps({"name": "chat_turn"}),
        },
        {
            "trace_id": "trace-error-456",
            "span_id": "span-456",
            "timestamp": (now - timedelta(minutes=2)).isoformat(),
            "service_name": "hoangvl-ai-chat",
            "name": "ai.chat.turn",
            "duration_ms": 5000,
            "raw_payload": json.dumps({"name": "chat_turn"}),
        },
    ]

    # ── signoz_metrics ────────────────────────────────────────
    metrics = [
        {
            "metric_id": "metric-4001",
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "service_name": "hoangvl-ai-chat",
            "metric_name": "chat.turns",
            "metric_value": 1.0,
            "unit": "count",
            "attributes": json.dumps({"status": "completed"}),
            "raw_payload": json.dumps({"metric_family": "chat.turns"}),
        },
        {
            "metric_id": "metric-4002",
            "timestamp": (now - timedelta(minutes=2)).isoformat(),
            "service_name": "hoangvl-ai-chat",
            "metric_name": "chat.turns",
            "metric_value": 1.0,
            "unit": "count",
            "attributes": json.dumps({"status": "failed"}),
            "raw_payload": json.dumps({"metric_family": "chat.turns"}),
        },
    ]

    # ── Upload all to MinIO ───────────────────────────────────
    datasets = {
        "user_interactions": interactions,
        "langfuse_generations": generations,
        "signoz_logs": logs,
        "signoz_traces": traces,
        "signoz_metrics": metrics,
    }

    for table_name, records in datasets.items():
        jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
        payload = jsonl.encode("utf-8")
        object_name = f"raw/{SOURCE_APP_ID}/{table_name}/{batch_id}.jsonl"
        mc.put_object(RAW_BUCKET, object_name, BytesIO(payload), len(payload),
                      content_type="application/jsonl")
        print(f"  ✓ Uploaded {len(records)} rows → s3://{RAW_BUCKET}/{object_name}")

    print(f"\nSeed complete! batch_id={batch_id}")
    print(f"Bucket: {RAW_BUCKET}")
    print(f"Source: {SOURCE_APP_ID}")


if __name__ == "__main__":
    main()
