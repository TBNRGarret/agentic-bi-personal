"""
Smoke DAG - S1.1 Infrastructure Verification.

Verifies connectivity to:
  1. Agentic BI ClickHouse (owned by this repo for gold tables)
  2. PostgreSQL DWH (reused from sprint-1 staging data)
  3. Langfuse API (reused from sprint-1 LLM traces)
  4. SigNoz ClickHouse (reused from sprint-1 APM telemetry)
"""

from datetime import datetime
import os

from airflow.sdk import dag, task


AGENTIC_CLICKHOUSE_HOST = os.getenv("AGENTIC_CLICKHOUSE_HOST", "agentic_clickhouse")
AGENTIC_CLICKHOUSE_PORT = int(os.getenv("AGENTIC_CLICKHOUSE_PORT", "8123"))
AGENTIC_CLICKHOUSE_USER = os.getenv("AGENTIC_CLICKHOUSE_USER", "default")
AGENTIC_CLICKHOUSE_PASSWORD = os.getenv("AGENTIC_CLICKHOUSE_PASSWORD", "securepassword")

POSTGRES_DWH_HOST = os.getenv("POSTGRES_DWH_HOST", "postgres_dwh")
POSTGRES_DWH_PORT = int(os.getenv("POSTGRES_DWH_PORT", "5432"))
POSTGRES_DWH_USER = os.getenv("POSTGRES_DWH_USER", "postgres")
POSTGRES_DWH_PASSWORD = os.getenv("POSTGRES_DWH_PASSWORD", "securepassword")
POSTGRES_DWH_DB = os.getenv("POSTGRES_DWH_DB", "dwh_db")

LANGFUSE_HEALTH_URL = os.getenv("LANGFUSE_HEALTH_URL", "http://langfuse_web:3000/api/public/health")
SIGNOZ_CLICKHOUSE_HOSTS = tuple(
    host.strip()
    for host in os.getenv("SIGNOZ_CLICKHOUSE_HOSTS", "signoz-clickhouse,chi-signoz-clickhouse-cluster0-0-0").split(",")
    if host.strip()
)


@dag(
    dag_id="smoke_dag",
    description="Verify connectivity to all infrastructure services",
    schedule=None,
    start_date=datetime(2026, 6, 8),
    catchup=False,
    tags=["infra", "smoke"],
    default_args={"retries": 2},
)
def smoke_dag():
    @task()
    def check_agentic_clickhouse():
        """Verify the ClickHouse instance owned by this Agentic BI project."""
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=AGENTIC_CLICKHOUSE_HOST,
            port=AGENTIC_CLICKHOUSE_PORT,
            username=AGENTIC_CLICKHOUSE_USER,
            password=AGENTIC_CLICKHOUSE_PASSWORD,
        )
        version = client.command("SELECT version()")
        print(f"[OK] Agentic ClickHouse connected - version {version}")

        client.command("CREATE DATABASE IF NOT EXISTS agentic_bi")
        databases = client.command("SHOW DATABASES")
        print(f"[OK] Databases: {databases}")
        return version

    @task()
    def check_postgres_dwh():
        """Verify the reused sprint-1 PostgreSQL DWH."""
        import psycopg2

        conn = psycopg2.connect(
            host=POSTGRES_DWH_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB,
        )
        cur = conn.cursor()
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        print(f"[OK] PostgreSQL DWH connected - {version}")

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'staging' LIMIT 5"
        )
        tables = [row[0] for row in cur.fetchall()]
        print(f"[OK] Staging tables found: {tables}")
        cur.close()
        conn.close()
        return version

    @task()
    def check_langfuse():
        """Verify the reused sprint-1 Langfuse web API."""
        import json
        import urllib.request

        req = urllib.request.Request(LANGFUSE_HEALTH_URL)
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        print(f"[OK] Langfuse API reachable - status: {data}")
        return data.get("status", "unknown")

    @task()
    def check_signoz_clickhouse():
        """Verify the reused sprint-1 SigNoz ClickHouse by explicit service names."""
        import urllib.request

        for host in SIGNOZ_CLICKHOUSE_HOSTS:
            try:
                resp = urllib.request.urlopen(f"http://{host}:8123/ping", timeout=3)
                if resp.status == 200:
                    print(f"[OK] SigNoz ClickHouse reachable at {host}:8123")
                    return host
            except Exception:
                continue

        raise RuntimeError(
            "SigNoz ClickHouse was not reachable by known service names. "
            "Connect Airflow to signoz-net and verify the SigNoz ClickHouse container name."
        )

    check_agentic_clickhouse()
    check_postgres_dwh()
    check_langfuse()
    check_signoz_clickhouse()


smoke_dag()
