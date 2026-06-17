#!/usr/bin/env python3
"""Publish Sprint 1 mart freshness properties to DataHub dataset metadata."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import clickhouse_connect
import httpx


DATAHUB_GMS_URL = os.getenv("DATAHUB_GMS_URL", "http://47.131.228.222:28080")
CLICKHOUSE_HOST = os.getenv("AGENTIC_VERIFY_CLICKHOUSE_HOST", "47.131.228.222")
CLICKHOUSE_PORT = int(os.getenv("AGENTIC_VERIFY_CLICKHOUSE_PORT", "18123"))
CLICKHOUSE_USER = os.getenv("AGENTIC_CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword")
PLATFORM_INSTANCE = os.getenv("DATAHUB_CLICKHOUSE_PLATFORM_INSTANCE", "agentic_clickhouse")

MART_DATASETS = {
    "fct_llm_interactions": {
        "freshness_query": "SELECT max(started_at) FROM vsf_mart.fct_llm_interactions",
        "description": "Business-ready LLM interaction fact table.",
    },
    "gold_app_daily_cost": {
        "freshness_query": "SELECT max(date) FROM vsf_mart.gold_app_daily_cost",
        "description": "Daily LLM cost, token, latency, and error mart.",
    },
    "data_quality_results": {
        "freshness_query": "SELECT max(executed_at) FROM vsf_mart.data_quality_results",
        "description": "Visible Sprint 1 data quality result history.",
    },
}


def json_default(value: Any) -> str | int | float:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def dataset_urn(table_name: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:clickhouse,{PLATFORM_INSTANCE}.vsf_mart.{table_name},PROD)"


def latest_dq_status(client) -> str:
    latest = client.command("SELECT max(executed_at) FROM vsf_mart.data_quality_results")
    if not latest:
        return "UNKNOWN"
    failures = client.command(
        "SELECT count() FROM vsf_mart.data_quality_results "
        "WHERE executed_at = (SELECT max(executed_at) FROM vsf_mart.data_quality_results) "
        "AND status = 'FAIL'"
    )
    return "FAIL" if failures else "PASS"


def latest_raw_load(client) -> str | None:
    value = client.command("SELECT max(load_finished_at) FROM vsf_raw.raw_ingestion_batches WHERE status = 'success'")
    return json_default(value) if value else None


def ingest_dataset_properties(urn: str, *, description: str, custom_properties: dict[str, str]) -> None:
    aspect = {
        "description": description,
        "customProperties": custom_properties,
    }
    payload = {
        "proposal": {
            "entityType": "dataset",
            "entityUrn": urn,
            "changeType": "UPSERT",
            "aspectName": "datasetProperties",
            "aspect": {
                "value": json.dumps(aspect, sort_keys=True),
                "contentType": "application/json",
            },
        }
    }
    response = httpx.post(f"{DATAHUB_GMS_URL}/aspects?action=ingestProposal", json=payload, timeout=20.0)
    response.raise_for_status()


def main() -> None:
    client = clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
    )
    dq_status = latest_dq_status(client)
    raw_load = latest_raw_load(client)
    published_at = datetime.now(timezone.utc).isoformat()

    for table_name, config in MART_DATASETS.items():
        latest_data = client.command(config["freshness_query"])
        custom = {
            "last_data_updated_at": json_default(latest_data) if latest_data else "",
            "latest_raw_load_finished_at": raw_load or "",
            "latest_successful_dag_run": raw_load or "",
            "latest_dq_status": dq_status,
            "freshness_published_at": published_at,
        }
        urn = dataset_urn(table_name)
        ingest_dataset_properties(urn, description=config["description"], custom_properties=custom)
        print(f"Published freshness for {urn}: {custom}")
    client.close()


if __name__ == "__main__":
    main()
