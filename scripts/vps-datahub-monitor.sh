#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose-datahub.yml}"
ENV_FILE="${ENV_FILE:-.env.datahub-vps}"
PROJECT_NAME="${PROJECT_NAME:-datahub-vps}"
MONITOR_SECONDS="${MONITOR_SECONDS:-900}"
MONITOR_INTERVAL_SECONDS="${MONITOR_INTERVAL_SECONDS:-15}"
LOG_DIR="${LOG_DIR:-logs/datahub-vps}"

mkdir -p "$LOG_DIR"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
stats_file="$LOG_DIR/docker-stats-$run_id.csv"
system_file="$LOG_DIR/system-memory-$run_id.csv"
state_file="$LOG_DIR/container-state-$run_id.log"

compose=(docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME")
if [[ -f "$ENV_FILE" ]]; then
  compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT_NAME")
fi

mapfile -t container_ids < <("${compose[@]}" ps -aq)
if (( ${#container_ids[@]} == 0 )); then
  echo "[FAIL] No containers found for project $PROJECT_NAME and file $COMPOSE_FILE." >&2
  exit 1
fi

echo "timestamp,container,name,cpu_percent,mem_usage,mem_limit,mem_percent,pids" > "$stats_file"
echo "timestamp,mem_total_mb,mem_used_mb,mem_free_mb,mem_shared_mb,mem_buff_cache_mb,mem_available_mb,swap_total_mb,swap_used_mb,swap_free_mb" > "$system_file"
: > "$state_file"

if command -v htop >/dev/null 2>&1; then
  echo "[INFO] Optional live htop view in another SSH session: htop"
else
  echo "[INFO] htop is not installed. Install with: sudo apt-get update && sudo apt-get install -y htop"
fi

echo "[INFO] Sampling DataHub VPS containers every ${MONITOR_INTERVAL_SECONDS}s for ${MONITOR_SECONDS}s."
echo "[INFO] Docker stats log: $stats_file"
echo "[INFO] System memory log: $system_file"
echo "[INFO] Container state log: $state_file"

end_time=$((SECONDS + MONITOR_SECONDS))
bad_state=0

while (( SECONDS <= end_time )); do
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  docker stats --no-stream --format '{{.Container}}|{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.PIDs}}' "${container_ids[@]}" |
    while IFS='|' read -r container name cpu mem mem_percent pids; do
      mem_usage="${mem%% / *}"
      mem_limit="${mem##* / }"
      printf '%s,%s,%s,%s,%s,%s,%s,%s\n' "$ts" "$container" "$name" "$cpu" "$mem_usage" "$mem_limit" "$mem_percent" "$pids" >> "$stats_file"
    done

  free -m | awk -v ts="$ts" '
    /^Mem:/ { mt=$2; mu=$3; mf=$4; ms=$5; mb=$6; ma=$7 }
    /^Swap:/ { st=$2; su=$3; sf=$4 }
    END { printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n", ts, mt, mu, mf, ms, mb, ma, st, su, sf }
  ' >> "$system_file"

  {
    echo "[$ts]"
    docker inspect --format '{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}}' "${container_ids[@]}"
  } >> "$state_file"

  while read -r line; do
    if [[ "$line" == *"/datahub-system-update "* && "$line" == *"status=exited"* && "$line" == *"ExitCode=0"* ]]; then
      continue
    fi
    if [[ "$line" == *"OOMKilled=true"* || "$line" == *"status=exited"* || "$line" == *"status=dead"* || "$line" == *"status=restarting"* ]]; then
      bad_state=1
    fi
    if [[ "$line" =~ RestartCount=([1-9][0-9]*) ]]; then
      bad_state=1
    fi
  done < <(tail -n "${#container_ids[@]}" "$state_file")

  if (( SECONDS < end_time )); then
    sleep "$MONITOR_INTERVAL_SECONDS"
  fi
done

echo "[INFO] Final container status:"
"${compose[@]}" ps

if (( bad_state != 0 )); then
  echo "[FAIL] OOM, restart, exited, dead, or restarting container evidence was captured."
  echo "[FAIL] Inspect $state_file and $stats_file before adding Airflow, ClickHouse, Langfuse, or SigNoz."
  exit 2
fi

echo "[OK] No OOM/restart/exited container evidence captured during the sampling window."
echo "[OK] Review $stats_file and $system_file for sustained memory headroom before running the full Agentic BI stack."
