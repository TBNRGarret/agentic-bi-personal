# Agentic BI -- Setup Guide

## Prerequisites

- Docker & Docker Compose (v2)
- Python 3.10+
- Git

## Quick Start

### 1. Clone the Repository

```bash
git clone <repo-url> agentic-bi
cd agentic-bi
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
# MinIO (remote - data source)
MINIO_ENDPOINT="http://<minio-host>:9000"
MINIO_ACCESS_KEY="<access_key>"
MINIO_SECRET_KEY="<secret_key>"
MINIO_BUCKET="telemetry-landing"

# PostgreSQL DWH
POSTGRES_DWH_DB=central_dwh
POSTGRES_DWH_USER=dwh_admin
POSTGRES_DWH_PASSWORD=dwh_admin_password
POSTGRES_DWH_PORT=5433

# Airflow metadata DB
POSTGRES_AIRFLOW_DB=airflow
POSTGRES_AIRFLOW_USER=airflow
POSTGRES_AIRFLOW_PASSWORD=airflow_password

# OpenAI (required for agent backend)
OPENAI_API_KEY="sk-..."
```

### 3. Start Services

```bash
docker compose up -d
```

This starts:
- `postgres-dwh` (port 5433) -- Central data warehouse
- `postgres-airflow` (port 5432) -- Airflow metadata DB
- `minio` (ports 9000, 9001) -- Object storage
- `metabase` (port 3000) -- BI dashboard
- `airflow-webserver` (port 8080) -- Airflow UI
- `airflow-scheduler` -- Task scheduler

### 4. Initialize Airflow

```bash
# Wait for Airflow to start (pip install takes 1-2 minutes)
# Check logs to confirm it's ready:
docker logs agentic-bi-airflow-webserver --tail 5

# Expected output: "Listening at: http://0.0.0.0:8080"
```

### 5. Configure Airflow Connection

```bash
docker exec agentic-bi-airflow-webserver airflow connections add 'minio_s3' \
    --conn-type 'aws' \
    --conn-login 'minio_admin' \
    --conn-password 'minio_admin_password' \
    --conn-extra '{"endpoint_url": "http://<minio-host>:9000", "region_name": "us-east-1"}'
```

### 6. Install dbt (Required for dbt_pipeline DAG)

```bash
# Install in webserver
docker exec agentic-bi-airflow-webserver python3 -m pip install "dbt-core<2.0" "dbt-postgres<2.0"

# Install in scheduler
docker exec agentic-bi-airflow-scheduler python3 -m pip install "dbt-core<2.0" "dbt-postgres<2.0"
```

### 7. Fix Permissions

```bash
# Airflow logs
chmod 777 airflow/logs/

# dbt project (for writing artifacts)
sudo chmod -R 777 dbt/
```

### 8. Verify Setup

```bash
# Check all containers
docker compose ps --all

# Check DWH schemas
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "\dn"
# Expected: raw, silver, gold schemas

# Check raw tables
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "\dt raw.*"
# Expected: 7 tables (llm_generations, llm_traces, system_logs, system_traces, system_metrics, source_app_registry, ingestion_batches)

# Open Airflow UI
open http://localhost:8080
# Login: admin / admin
```

---

## Running the Pipelines

### 1. Ingest Data from MinIO

Trigger the ingestion DAG to load Parquet files from remote MinIO:

```bash
docker exec agentic-bi-airflow-webserver airflow dags trigger ingest_minio_to_dwh
```

Check status:
```bash
docker exec agentic-bi-airflow-webserver airflow dags list-runs -d ingest_minio_to_dwh
```

Verify data loaded:
```bash
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "SELECT source_owner, COUNT(*) FROM raw.llm_generations GROUP BY source_owner;"
```

### 2. Run dbt Transformations

Build staging views + gold tables:

```bash
docker exec agentic-bi-airflow-webserver bash -c "cd /opt/airflow/dbt && dbt build --profiles-dir ."
```

Run data quality tests only:
```bash
docker exec agentic-bi-airflow-webserver bash -c "cd /opt/airflow/dbt && dbt test --profiles-dir ."
```

### 3. Trigger Automated Pipeline

```bash
docker exec agentic-bi-airflow-webserver airflow dags trigger dbt_pipeline
```

This runs: `dbt build` -> `dbt test` on an hourly schedule.

---

## Accessing Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Airflow | `http://localhost:8080` | admin / admin |
| Metabase | `http://localhost:3000` | admin@admin.com / metabase1 |
| MinIO Console | `http://localhost:9001` | minio_admin / minio_admin_password |
| DWH (Postgres) | `localhost:5433` | dwh_admin / dwh_admin_password |

### Metabase Database Setup

After first login, add a database connection:

| Field | Value |
|-------|-------|
| Type | PostgreSQL |
| Host | `postgres-dwh` |
| Port | `5432` |
| Database | `central_dwh` |
| Username | `dwh_admin` |
| Password | `dwh_admin_password` |

---

## Useful Commands

```bash
# Start/Stop
make up          # Start all services
make down        # Stop all services
make reset       # Reset volumes + restart

# Query DWH
make psql-dwh    # Connect to DWH via psql

# Check logs
make logs        # Tail all container logs

# Inspect MinIO data
make inspect     # Scan MinIO bucket structure

# Check container status
make status      # docker compose ps
```

---

## Troubleshooting

### Port 5432 Already in Use
```bash
# Stop host PostgreSQL temporarily
sudo systemctl stop postgresql
# Or change postgres-airflow port in docker-compose.yml
```

### Permission Denied for Logs
```bash
chmod 777 airflow/logs/
```

### Permission Denied for dbt
```bash
sudo chmod -R 777 dbt/
```

### dbt Not Found
```bash
docker exec agentic-bi-airflow-webserver python3 -m pip install "dbt-core<2.0" "dbt-postgres<2.0"
docker exec agentic-bi-airflow-scheduler python3 -m pip install "dbt-core<2.0" "dbt-postgres<2.0"
```

### Airflow Cannot Connect to MinIO
```bash
# Verify connection exists
docker exec agentic-bi-airflow-webserver airflow connections list

# Re-add if missing
docker exec agentic-bi-airflow-webserver airflow connections add 'minio_s3' \
    --conn-type 'aws' \
    --conn-login 'minio_admin' \
    --conn-password 'minio_admin_password' \
    --conn-extra '{"endpoint_url": "http://<minio-host>:9000", "region_name": "us-east-1"}'
```

### "None" String in Numeric Columns
Raw data may contain string `"None"` instead of NULL. The staging models handle this with `NULLIF(column, 'None')`.

---

## Project Structure

```
agentic-bi/
├── docker-compose.yml       # Local development stack
├── .env                     # Environment variables
├── Makefile                 # Common commands
├── dwh/init/init.sql        # DWH schema initialization
├── airflow/
│   └── dags/
│       ├── smoke_test.py           # Health check
│       ├── ingest_minio_dag.py     # MinIO ingestion
│       └── dbt_pipeline_dag.py     # dbt orchestration
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── sources.yml             # Source definitions
│       ├── staging/                 # Silver layer (5 models)
│       └── marts/                   # Gold layer (5 models)
├── backend/                # FastAPI (Day 4)
├── frontend/               # Next.js (Day 5)
├── infra/
│   └── airflow/requirements.txt
├── scripts/
│   └── inspect_minio_data.py
└── docs/
    ├── report.md           # Daily progress report
    └── setup.md            # This guide
```
