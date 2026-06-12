#!/usr/bin/env bash
# Connect Agentic BI Astro CLI Airflow containers to external Docker networks.
# Run AFTER `astro dev start` completes successfully.
# Bash equivalent of scripts/connect-networks.ps1
set -euo pipefail

# Base networks to bridge
NETWORKS=("dwh_network")

# Auto-detect DataHub network
DATAHUB_NET=$(docker network ls --format '{{.Name}}' | grep -i datahub | head -n 1 || true)
if [[ -n "$DATAHUB_NET" ]]; then
  NETWORKS+=("$DATAHUB_NET")
  echo "Auto-detected DataHub network: $DATAHUB_NET"
else
  echo "No DataHub network found - skipping DataHub bridge"
fi

# Find Airflow containers created by this Astro project
mapfile -t CONTAINERS < <(docker ps --format '{{.Names}}' | grep -E '^agentic-bi_.*-(scheduler|api-server|triggerer|dag-processor)-[0-9]+$' || true)

if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
  echo "ERROR: No Airflow containers found. Run 'astro dev start' first."
  exit 1
fi

echo "Found ${#CONTAINERS[@]} Airflow containers: ${CONTAINERS[*]}"

for net in "${NETWORKS[@]}"; do
  # Check if network exists
  if ! docker network ls --format '{{.Name}}' | grep -qx "$net"; then
    echo "SKIP: Network '$net' does not exist"
    continue
  fi
  for c in "${CONTAINERS[@]}"; do
    if docker network connect "$net" "$c" 2>/dev/null; then
      echo "OK: Connected $c -> $net"
    else
      echo "SKIP: $c already on $net (or error)"
    fi
  done
done

echo "Done. Agentic BI Airflow DAGs can now reach external services by container name."
