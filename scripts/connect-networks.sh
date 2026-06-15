#!/usr/bin/env bash
# Connect Agentic BI Astro Airflow containers to the optional DataHub network.
set -euo pipefail

DATAHUB_NET=$(docker network ls --format '{{.Name}}' | grep -i datahub | head -n 1 || true)
if [[ -z "$DATAHUB_NET" ]]; then
  echo "No DataHub network found - skipping optional DataHub bridge"
  exit 0
fi

mapfile -t CONTAINERS < <(docker ps --format '{{.Names}}' | grep -E '^agentic-bi_.*-(scheduler|api-server|triggerer|dag-processor)-[0-9]+$' || true)
if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
  echo "ERROR: No Airflow containers found. Run 'astro dev start' first."
  exit 1
fi

for c in "${CONTAINERS[@]}"; do
  if docker network connect "$DATAHUB_NET" "$c" 2>/dev/null; then
    echo "OK: Connected $c -> $DATAHUB_NET"
  else
    echo "SKIP: $c already on $DATAHUB_NET (or error)"
  fi
done

echo "Done. DataHub bridge checked."
