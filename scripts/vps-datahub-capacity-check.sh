#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose-datahub.yml}"
ENV_FILE="${ENV_FILE:-.env.datahub-vps}"
PROJECT_NAME="${PROJECT_NAME:-datahub-vps}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-420}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[FAIL] Missing $ENV_FILE."
  echo "       Copy .env.datahub-vps.example to .env.datahub-vps and replace the change-me secrets."
  exit 1
fi

env_value() {
  local key="$1"
  local fallback="$2"
  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d '=' -f 2- | tr -d '\r')"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$fallback"
  fi
}

DATAHUB_MAPPED_GMS_PORT="${DATAHUB_MAPPED_GMS_PORT:-$(env_value DATAHUB_MAPPED_GMS_PORT 28080)}"
MONITOR_SECONDS="${MONITOR_SECONDS:-$(env_value MONITOR_SECONDS 900)}"
MONITOR_INTERVAL_SECONDS="${MONITOR_INTERVAL_SECONDS:-$(env_value MONITOR_INTERVAL_SECONDS 15)}"
DATAHUB_GMS_HEALTH_URL="${DATAHUB_GMS_HEALTH_URL:-http://localhost:${DATAHUB_MAPPED_GMS_PORT:-28080}/health}"

if command -v sysctl >/dev/null 2>&1; then
  max_map_count="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
  if (( max_map_count < 262144 )); then
    echo "[FAIL] vm.max_map_count=$max_map_count is too low for OpenSearch."
    echo "       Run: sudo sysctl -w vm.max_map_count=262144"
    echo "       Persist: echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-datahub.conf"
    exit 1
  fi
fi

echo "[START] Starting DataHub VPS stack..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d

echo "[WAIT] Waiting for DataHub GMS health at $DATAHUB_GMS_HEALTH_URL"
deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
until curl -fsS "$DATAHUB_GMS_HEALTH_URL" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "[FAIL] DataHub GMS did not become healthy within ${HEALTH_TIMEOUT_SECONDS}s."
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps
    exit 2
  fi
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps --services --filter status=exited | sed 's/^/[WARN] Exited service: /' || true
  sleep 5
done

echo "[OK] DataHub GMS is healthy."
echo "[CHECK] Running htop-style Docker/system memory monitor..."
MONITOR_SECONDS="$MONITOR_SECONDS" \
MONITOR_INTERVAL_SECONDS="$MONITOR_INTERVAL_SECONDS" \
COMPOSE_FILE="$COMPOSE_FILE" \
ENV_FILE="$ENV_FILE" \
PROJECT_NAME="$PROJECT_NAME" \
  bash scripts/vps-datahub-monitor.sh

echo "[DONE] Capacity check complete. Keep the generated logs as the evidence for whether this VPS can hold DataHub before adding the rest of Agentic BI."
