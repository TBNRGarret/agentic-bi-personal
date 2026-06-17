#!/usr/bin/env python3
"""Strict Sprint 1 acceptance verification for the local Agentic BI stack."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import clickhouse_connect
from minio import Minio

from metadata.client import DataHubClient


ROOT = Path(__file__).resolve().parents[1]
RAW_BUCKET = os.getenv("MINIO_BUCKET", "agentic-bi-raw")
EXPECTED_RAW_TABLES = {
    "langfuse_generations",
    "user_interactions",
    "signoz_logs",
    "signoz_traces",
    "signoz_metrics",
}
REQUIRED_TABLES = {
    "vsf_raw": ["raw_ingestion_batches", "langfuse_generations", "user_interactions", "signoz_logs", "signoz_traces", "signoz_metrics"],
    "vsf_stg": ["staging_llm_generations", "stg_langfuse_generations", "stg_user_interactions", "stg_signoz_logs"],
    "vsf_mart": ["fct_llm_interactions", "gold_app_daily_cost", "data_quality_results"],
}
REQUIRED_DBT_ARTIFACTS = ["manifest.json", "catalog.json", "run_results.json", "sources.json"]


class CheckFailed(RuntimeError):
    pass


class Verifier:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"[OK] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[FAIL] {message}")

    def check(self, name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - verifier should collect all failures.
            self.fail(f"{name}: {exc}")

    def finish(self) -> int:
        print("\nVerification summary")
        print(f"  failures: {len(self.failures)}")
        print(f"  warnings: {len(self.warnings)}")
        if self.failures:
            return 1
        return 0


def http_status(url: str, timeout: int = 10) -> int:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - local verifier URLs only.
            return response.status
    except URLError as exc:
        raise CheckFailed(f"{url} not reachable: {exc}") from exc


def run_command(args: list[str]) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise CheckFailed(f"Command failed ({' '.join(args)}): {result.stderr or result.stdout}")
    return result.stdout


def docker_container(pattern: str) -> str:
    names = run_command(["docker", "ps", "--format", "{{.Names}}"])
    for name in names.splitlines():
        if re.match(pattern, name):
            return name
    raise CheckFailed(f"No container matched {pattern}")


def airflow_json(output: str) -> Any:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if (line.startswith("[") and line.endswith("]")) or (line.startswith("{") and line.endswith("}")):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    raise CheckFailed(f"Airflow CLI did not return JSON: {output}")


def clickhouse_client():
    return clickhouse_connect.get_client(
        host=os.getenv("AGENTIC_VERIFY_CLICKHOUSE_HOST", "47.131.228.222"),
        port=int(os.getenv("AGENTIC_VERIFY_CLICKHOUSE_PORT", "18123")),
        username=os.getenv("AGENTIC_CLICKHOUSE_USER", "default"),
        password=os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword"),
    )


def minio_client():
    return Minio(
        os.getenv("MINIO_ENDPOINT", "47.131.228.222:19001"),
        access_key=os.getenv("MINIO_ROOT_USER", "minioadmin"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "minioadmin"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def check_services(v: Verifier) -> None:
    endpoints = {
        "Airflow API server": "http://47.131.228.222:18080/api/v2/monitor/health",
        "ClickHouse HTTP": "http://47.131.228.222:18123/ping",
        "MinIO API": "http://47.131.228.222:19001/minio/health/live",
        "MinIO Console": "http://47.131.228.222:19002",
        "DataHub UI": "http://47.131.228.222:9002",
        "DataHub GMS": "http://47.131.228.222:28080/health",
    }
    for name, url in endpoints.items():
        status = http_status(url)
        if status < 200 or status >= 400:
            raise CheckFailed(f"{name} returned HTTP {status}")
        v.ok(f"{name} reachable at {url} (HTTP {status})")


def check_minio(v: Verifier) -> None:
    client = minio_client()
    if not client.bucket_exists(RAW_BUCKET):
        raise CheckFailed(f"Bucket {RAW_BUCKET} does not exist")
    objects = list(client.list_objects(RAW_BUCKET, prefix="raw/", recursive=True))
    object_names = {obj.object_name for obj in objects if obj.object_name}
    manifests = sorted(name for name in object_names if "/_manifests/" in name and name.endswith(".json"))
    if len(manifests) < 4:
        raise CheckFailed(f"Expected at least 4 source app manifests, found {len(manifests)}")
    for manifest_name in manifests:
        response = client.get_object(RAW_BUCKET, manifest_name)
        try:
            manifest = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
            response.release_conn()
        tables = {item.get("source_table") for item in manifest.get("objects", [])}
        if tables != EXPECTED_RAW_TABLES:
            raise CheckFailed(f"Manifest {manifest_name} has tables {sorted(tables)}, expected {sorted(EXPECTED_RAW_TABLES)}")
        for item in manifest["objects"]:
            object_name = item["object_name"]
            if object_name not in object_names:
                raise CheckFailed(f"Manifest {manifest_name} references missing object {object_name}")
            response = client.get_object(RAW_BUCKET, object_name)
            try:
                line_count = sum(1 for line in response.read().decode("utf-8").splitlines() if line.strip())
            finally:
                response.close()
                response.release_conn()
            if line_count != item["row_count"]:
                raise CheckFailed(f"{object_name} line count {line_count} != manifest {item['row_count']}")
    v.ok(f"MinIO bucket {RAW_BUCKET} contains {len(manifests)} valid manifests and {len(object_names)} raw objects")


def check_clickhouse(v: Verifier) -> None:
    client = clickhouse_client()
    try:
        for database, tables in REQUIRED_TABLES.items():
            rows = client.query(f"SHOW TABLES FROM {database}").result_rows
            existing = {row[0] for row in rows}
            missing = set(tables) - existing
            if missing:
                raise CheckFailed(f"{database} missing tables: {sorted(missing)}")
        row_checks = {
            "vsf_raw.langfuse_generations": 100,
            "vsf_raw.user_interactions": 100,
            "vsf_mart.fct_llm_interactions": 100,
            "vsf_mart.gold_app_daily_cost": 1,
            "vsf_mart.data_quality_results": 1,
        }
        for table_name, minimum in row_checks.items():
            count = client.command(f"SELECT count() FROM {table_name}")
            if count < minimum:
                raise CheckFailed(f"{table_name} row count {count} < {minimum}")
        latest_dq_failures = client.command(
            "SELECT count() FROM vsf_mart.data_quality_results "
            "WHERE executed_at = (SELECT max(executed_at) FROM vsf_mart.data_quality_results) "
            "AND status = 'FAIL'"
        )
        if latest_dq_failures:
            raise CheckFailed(f"Latest DQ run has {latest_dq_failures} failing checks")
        v.ok("ClickHouse required tables, row counts, and latest DQ status verified")
    finally:
        client.close()


def check_airflow(v: Verifier) -> None:
    scheduler = docker_container(r"^agentic-bi_.*-scheduler-[0-9]+$")
    output = run_command(["docker", "exec", scheduler, "airflow", "dags", "list-runs", "agentic_bi_pipeline", "-o", "json"])
    runs = airflow_json(output)
    if not runs:
        raise CheckFailed("No agentic_bi_pipeline DAG runs found")
    latest = runs[0]
    if latest.get("state") != "success":
        raise CheckFailed(f"Latest agentic_bi_pipeline run is {latest.get('state')}: {latest.get('run_id')}")
    v.ok(f"Latest agentic_bi_pipeline DAG run succeeded: {latest.get('run_id')}")


def check_dbt_artifacts(v: Verifier) -> None:
    target = ROOT / "include" / "dbt_project" / "target"
    missing = [name for name in REQUIRED_DBT_ARTIFACTS if not (target / name).exists()]
    if missing:
        raise CheckFailed(f"Missing dbt artifacts: {missing}")
    v.ok("dbt manifest, catalog, run_results, and sources artifacts exist")


def check_datahub(v: Verifier) -> None:
    client = DataHubClient()
    tables = client.list_tables(schema="vsf_mart")
    table_names = {table["name"] for table in tables}
    required = {"fct_llm_interactions", "gold_app_daily_cost", "data_quality_results"}
    missing = required - table_names
    if missing:
        raise CheckFailed(f"DataHub missing mart tables from client discovery: {sorted(missing)}")
    gold = next(table for table in tables if table["name"] == "gold_app_daily_cost")
    columns = client.get_columns(gold["urn"])
    column_names = {column["name"] for column in columns}
    if not {"date", "source_app_id", "total_cost_usd", "error_rate"}.issubset(column_names):
        raise CheckFailed(f"DataHub gold_app_daily_cost columns incomplete: {sorted(column_names)}")
    lineage = client.get_lineage(gold["urn"])
    if not lineage["upstream"]:
        raise CheckFailed("DataHub lineage query returned no upstream datasets")
    freshness = client.get_freshness(gold["urn"])
    if not freshness.get("last_updated_iso"):
        raise CheckFailed("DataHub freshness query returned no lastIngested timestamp")
    if not freshness.get("last_data_updated_at"):
        raise CheckFailed("DataHub dataset custom freshness property last_data_updated_at is missing")
    v.ok("DataHub client discovers mart tables, columns, lineage, and freshness")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Sprint 1 Agentic BI local acceptance criteria.")
    parser.parse_args()
    verifier = Verifier()
    checks = [
        ("service health", lambda: check_services(verifier)),
        ("MinIO raw manifests", lambda: check_minio(verifier)),
        ("Airflow DAG success", lambda: check_airflow(verifier)),
        ("ClickHouse warehouse", lambda: check_clickhouse(verifier)),
        ("dbt artifacts", lambda: check_dbt_artifacts(verifier)),
        ("DataHub metadata", lambda: check_datahub(verifier)),
    ]
    for name, fn in checks:
        verifier.check(name, fn)
    return verifier.finish()


if __name__ == "__main__":
    sys.exit(main())
