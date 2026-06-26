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

# ── Expose Astro API server via socat ────────────────────────
FORWARD_PORT=18080
if command -v socat &>/dev/null; then
  if ! ss -tlnp | grep -q ":$FORWARD_PORT "; then
    nohup socat TCP-LISTEN:"$FORWARD_PORT",fork,reuseaddr TCP:127.0.0.1:8080 > /tmp/socat-airflow.log 2>&1 &
    echo "socat: forwarding :$FORWARD_PORT -> 127.0.0.1:8080 (PID $!)"
  else
    echo "socat: port $FORWARD_PORT already forwarded"
  fi
else
  echo "WARNING: socat not found. Install it with: sudo apt-get install -y socat"
fi

echo ""
echo "Agentic BI local stack started."
echo "Airflow UI: http://47.131.228.222:$FORWARD_PORT"
echo "Agentic ClickHouse HTTP: http://47.131.228.222:18123/ping"
echo "MinIO API: http://47.131.228.222:19001"
echo "MinIO Console: http://47.131.228.222:19002"
echo "Run verification: ./scripts/verify-system.sh"
