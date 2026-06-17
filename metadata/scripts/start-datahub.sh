#!/usr/bin/env bash
# Start the pinned local DataHub stack for Sprint 1 metadata.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ ! -f .env.datahub-vps ]]; then
  cp .env.datahub-vps.example .env.datahub-vps
  echo "[INFO] Created .env.datahub-vps from example for local DataHub defaults."
fi

docker compose --env-file .env.datahub-vps -f docker-compose-datahub.yml -p datahub-vps up -d

echo "[WAIT] Waiting for DataHub GMS at http://localhost:28080/health..."
for _ in $(seq 1 90); do
  if curl -fsS --max-time 5 http://localhost:28080/health >/dev/null; then
    ./scripts/connect-networks.sh
    echo "DataHub started successfully."
    echo "UI:      http://localhost:9002   (datahub/datahub)"
    echo "GMS API: http://localhost:28080"
    echo "Run metadata ingestion: ./metadata/scripts/run-ingestion.sh"
    exit 0
  fi
  sleep 5
done

echo "ERROR: DataHub GMS did not become healthy at http://localhost:28080/health"
exit 1
