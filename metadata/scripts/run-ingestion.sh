#!/usr/bin/env bash
# Generate dbt artifacts, ingest dbt + ClickHouse metadata, and publish freshness to DataHub.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

scheduler="$(docker ps --format '{{.Names}}' | grep -E '^agentic-bi_.*-scheduler-[0-9]+$' | head -n 1)"
if [[ -z "$scheduler" ]]; then
  echo "ERROR: Could not find Agentic BI Airflow scheduler container."
  exit 1
fi

if ! curl -fsS --max-time 10 http://localhost:28080/health >/dev/null; then
  echo "ERROR: DataHub GMS is not reachable at http://localhost:28080."
  exit 1
fi

if command -v datahub >/dev/null 2>&1; then
  DATAHUB_CMD="datahub"
else
  echo "[INSTALL] Installing acryl-datahub CLI..."
  python -m pip install 'acryl-datahub'
  DATAHUB_CMD="datahub"
fi

if [[ -x .venv/bin/python ]]; then
  PYTHON_CMD=".venv/bin/python"
else
  PYTHON_CMD="python"
fi

echo "[DBT] Running dbt source freshness..."
docker exec "$scheduler" dbt source freshness --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project

echo "[DBT] Running dbt build..."
docker exec "$scheduler" dbt build --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project

echo "[DBT] Running dbt docs generate..."
docker exec "$scheduler" dbt docs generate --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project

for artifact in manifest.json catalog.json run_results.json sources.json; do
  test -f "include/dbt_project/target/$artifact" || { echo "ERROR: Missing include/dbt_project/target/$artifact"; exit 1; }
  echo "[OK] Found include/dbt_project/target/$artifact"
done

echo "[DATAHUB] Ingesting dbt metadata..."
$DATAHUB_CMD ingest -c metadata/recipes/dbt_recipe.yml

echo "[DATAHUB] Ingesting ClickHouse metadata..."
$DATAHUB_CMD ingest -c metadata/recipes/clickhouse_recipe.yml

if [ -f ".env" ]; then
  # Load env variables for Metabase ingestion if run locally
  export $(grep -E '^(METABASE_ADMIN_EMAIL|METABASE_ADMIN_PASSWORD)=' .env | xargs)
fi

echo "[DATAHUB] Ingesting Metabase metadata..."
$DATAHUB_CMD ingest -c metadata/recipes/metabase_recipe.yml

echo "[DATAHUB] Publishing mart freshness properties..."
$PYTHON_CMD metadata/scripts/publish_freshness.py

echo "Metadata ingestion completed."

