#!/usr/bin/env bash
# Start the local Agentic BI Airflow stack and print service URLs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

astro dev start
./scripts/connect-networks.sh

API_SERVER=$(docker ps --format '{{.Names}}' | grep -E '^agentic-bi_.*-api-server-[0-9]+$' | head -n 1)
if [[ -z "$API_SERVER" ]]; then
  echo "ERROR: Could not find Agentic BI Airflow API server container."
  exit 1
fi
AIRFLOW_PORT=$(docker port "$API_SERVER" 8080/tcp | head -n 1 | awk -F: '{print $NF}')

echo "Agentic BI local stack started."
echo "Airflow UI: http://47.131.228.222:18080"
echo "Agentic ClickHouse HTTP: http://47.131.228.222:18123/ping"
echo "MinIO API: http://47.131.228.222:19001"
echo "MinIO Console: http://47.131.228.222:19002"
echo "Run verification: ./scripts/verify-system.sh"
