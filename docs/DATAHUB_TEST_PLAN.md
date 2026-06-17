# DataHub VPS Capacity Test Plan

This plan tests only the DataHub service group on the VPS before adding
Airflow, ClickHouse, Langfuse, SigNoz, or the Observation Service. The goal is
to prove whether the VPS can hold the metadata catalog without OOM kills,
container restarts, or sustained swap pressure.

The current runnable artifacts are:

- `docker-compose-datahub.yml`
- `.env.datahub-vps.example`
- `scripts/vps-datahub-capacity-check.sh`
- `scripts/vps-datahub-monitor.sh`

## 1. Prepare Swap On The VPS

Run this once on the VPS. Skip the `fallocate` step if an existing swap file is
already configured.

```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl vm.swappiness=10
sudo sysctl -w vm.max_map_count=262144
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-datahub.conf
free -h
```

`vm.max_map_count` is required by OpenSearch. The capacity script checks it
before starting containers so a low kernel limit fails fast instead of looking
like a DataHub crash.

## 2. Prepare DataHub Environment

```bash
cp .env.datahub-vps.example .env.datahub-vps
openssl rand -base64 32
```

Replace every `change-me` value in `.env.datahub-vps` with generated values.
Keep this file out of git.

## 3. Start The Isolated DataHub Stack

One-shot capacity check:

```bash
bash scripts/vps-datahub-capacity-check.sh
```

Manual start command, useful when you want to monitor in separate SSH sessions:

```bash
docker compose --env-file .env.datahub-vps -f docker-compose-datahub.yml -p datahub-vps up -d
```

Expected UI/API:

- DataHub UI: `http://<VPS_IP>:9002`
- DataHub GMS health: `http://<VPS_IP>:28080/health`
- Default quickstart user: `datahub` / `datahub`

## 4. Monitor RAM, Swap, And Crashes

Run the non-interactive monitor for at least 15 minutes:

```bash
MONITOR_SECONDS=900 MONITOR_INTERVAL_SECONDS=15 bash scripts/vps-datahub-monitor.sh
```

The monitor writes:

- Docker memory samples from `docker stats --no-stream`
- System memory and swap samples from `free -m`
- Container state evidence including `OOMKilled` and `RestartCount`

Optional live terminal view:

```bash
htop
```

Pass criteria:

- No container has `OOMKilled=true`
- No long-running DataHub service is `exited`, `dead`, or `restarting`
- `datahub-system-update` may be `exited` only when `ExitCode=0`, because it is a one-shot bootstrap job
- `RestartCount` stays `0` during the sampling window
- Swap usage remains low and does not continuously climb
- GMS and UI stay reachable after clicking through search and lineage screens

## 5. Stop Or Reset The Test Stack

Stop while keeping volumes:

```bash
docker compose --env-file .env.datahub-vps -f docker-compose-datahub.yml -p datahub-vps down
```

Full reset:

```bash
docker compose --env-file .env.datahub-vps -f docker-compose-datahub.yml -p datahub-vps down -v
```

Only after this isolated test passes should the VPS attempt the full Agentic BI
stack with Airflow, ClickHouse, Langfuse, and SigNoz.
