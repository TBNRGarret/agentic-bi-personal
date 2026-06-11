# Day 1 - Scaffold Project + Local Infrastructure

**Date**: June 11, 2026

---

## Summary

Successfully scaffolded the project structure and deployed the full local stack with Docker Compose, including Airflow services (webserver + scheduler), PostgreSQL databases (DWH + Airflow metadata), MinIO object storage, and Metabase BI.

### Services Running

| Service | Container Name | Status | Port |
|---------|---------------|--------|------|
| PostgreSQL DWH | agentic-bi-postgres-dwh | Healthy | 5433 |
| PostgreSQL Airflow | agentic-bi-postgres-airflow | Healthy | 5432 |
| MinIO | agentic-bi-minio | Running | 9000, 9001 |
| PostgreSQL Metabase | agentic-bi-metabase-db | Healthy | - |
| Metabase | agentic-bi-metabase | Running | 3000 |
| Airflow Webserver | agentic-bi-airflow-webserver | Running | 8080 |
| Airflow Scheduler | agentic-bi-airflow-scheduler | Running | - |

### DWH Schema Initialized

- **Schemas**: `raw`, `silver`, `gold`
- **Raw tables**: `llm_generations`, `llm_traces`, `system_logs`, `system_traces`, `system_metrics`
- **Tracking tables**: `source_app_registry`, `ingestion_batches`
- **Registered sources**: AN, BACH, HOANG, TUAN

---

## Issues Fixed

### 1. Port Conflict (5432)
Host PostgreSQL was already using port 5432. Changed `postgres-airflow` host mapping from `5432:5432` to `5434:5432` temporarily. Later stopped host PostgreSQL and reverted to `5432:5432`.

### 2. Airflow Logs Permission Denied
Airflow container runs as UID 50000 (`airflow`), but `./airflow/logs/` was owned by host user. Fixed with:
```bash
chmod 777 airflow/logs/
```

### 3. S3Hook `list_buckets()` Method Not Found
The `S3Hook` provider does not have a `list_buckets()` method. Replaced with `check_for_bucket()`.

---

## Test Commands & Results

### Start Stack
```bash
docker compose up -d
```

### Verify All Containers
```bash
docker compose ps --all
```

### Check DWH Schemas
```bash
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "\dn"
# Output: raw, silver, gold
```

### Check Raw Tables
```bash
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "\dt raw.*"
# Output: 7 tables
```

### Check Source Registry
```bash
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "SELECT source_owner, is_active FROM raw.source_app_registry;"
# Output: AN, BACH, HOANG, TUAN (all active)
```

### Trigger Smoke Test DAG
```bash
docker exec agentic-bi-airflow-webserver airflow dags trigger smoke_test
```

### Check DAG Run Status
```bash
docker exec agentic-bi-airflow-webserver airflow dags list-runs -d smoke_test
```

### Check Task States
```bash
docker exec agentic-bi-airflow-webserver airflow tasks states-for-dag-run smoke_test <run_id>
```

### Airflow Web UI
```
URL: http://localhost:8080
Login: admin / admin
```

### Quick Logs
```bash
docker compose logs -f
```

---

## Smoke Test Result

| Task | State |
|------|-------|
| `check_s3` | success - MinIO bucket 'telemetry-landing' found |
| `check_dwh` | success - DWH schemas + tables verified |

All DAG runs completed with `success` status.

---

## Files Created / Modified

| File | Action |
|------|--------|
| `docker-compose.yml` | Modified - added Airflow services |
| `dwh/init/init.sql` | Created - schemas, raw tables, seed data |
| `.env` | Modified - added PostgreSQL, OpenAI config |
| `Makefile` | Created - up/down/psql/logs commands |
| `infra/airflow/requirements.txt` | Created - Python dependencies |
| `airflow/dags/smoke_test.py` | Created - health check DAG |
| `docs/report.md` | Created - this report |

---

# Day 2 -- MinIO S3 Ingestion + dbt Project Init

**Date**: June 11, 2026

---

## Summary

Connected to remote MinIO S3, ingested Parquet data into PostgreSQL raw tables via Airflow DAG, and initialized dbt project with 5 staging models.

### New Services / Config Added

| Item | Status | Notes |
|------|--------|-------|
| dbt volume mount in Airflow | Done | `./dbt:/opt/airflow/dbt` added to both containers |
| MinIO Airflow connection | Done | `minio_s3` added via `airflow connections add` |
| dbt-postgres | Done | Installed in both webserver + scheduler containers |
| Ingestion DAG | Done | `ingest_minio_to_dwh` - hourly schedule |
| dbt project | Done | 5 staging models, profiles.yml, sources.yml |

---

## Issues Fixed

### 1. dbt Not Installed in Airflow Container
`dbt` command not found because `dbt-postgres` was not in the base image. Fixed by:
```bash
docker exec agentic-bi-airflow-webserver python3 -m pip install "dbt-core<2.0" "dbt-postgres<2.0"
```

### 2. dbt Permission Denied on target/
dbt running as UID 50000 could not write to host-mounted `dbt/` directory. Fixed with:
```bash
sudo chmod -R 777 dbt/
```

### 3. Schema Double-Naming (silver_silver)
`profiles.yml` had `schema: silver` and `dbt_project.yml` had `+schema: silver` for staging, causing dbt to create `silver_silver`. Fixed by removing `+schema: silver` from staging config.

---

## Test Commands

### Trigger Ingestion DAG
```bash
docker exec agentic-bi-airflow-webserver airflow dags trigger ingest_minio_to_dwh
```

### Check Ingestion Status
```bash
docker exec agentic-bi-airflow-webserver airflow dags list-runs -d ingest_minio_to_dwh
docker exec agentic-bi-airflow-webserver airflow tasks states-for-dag-run ingest_minio_to_dwh <run_id>
```

### Verify Data Loaded
```bash
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "SELECT source_owner, COUNT(*) FROM raw.llm_generations GROUP BY source_owner;"
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "SELECT status, COUNT(*) FROM raw.ingestion_batches GROUP BY status;"
```

### Run dbt Build
```bash
docker exec agentic-bi-airflow-webserver bash -c "cd /opt/airflow/dbt && dbt build --profiles-dir ."
```

### Verify Staging Views
```bash
docker exec -it agentic-bi-postgres-dwh psql -U dwh_admin -d central_dwh -c "\dv silver.*"
```

### Add Airflow Connection Manually
```bash
docker exec agentic-bi-airflow-webserver airflow connections add 'minio_s3' \
    --conn-type 'aws' \
    --conn-login 'minio_admin' \
    --conn-password 'minio_admin_password' \
    --conn-extra '{"endpoint_url": "http://47.131.228.222:9000", "region_name": "us-east-1"}'
```

---

## Ingestion DAG Result

| Table | Data Loaded |
|-------|------------|
| `raw.llm_generations` | AN: 191, BACH: 24, HOANG: 7 rows |
| `raw.llm_traces` | AN: 188, BACH: 4, HOANG: 7, TUAN: 47 rows |
| `raw.system_logs` | AN: 23651, BACH: 65, HOANG: 20, TUAN: 620 rows |
| `raw.system_traces` | AN: 175823, BACH: 63, HOANG: 30, TUAN: 251 rows |
| `raw.system_metrics` | HOANG: 52 rows |

All batches tracked in `raw.ingestion_batches` with status LOADED.

## dbt Build Result

| Model | Type | Status |
|-------|------|--------|
| `silver.stg_llm_generations` | View | OK |
| `silver.stg_llm_traces` | View | OK |
| `silver.stg_system_logs` | View | OK |
| `silver.stg_system_traces` | View | OK |
| `silver.stg_system_metrics` | View | OK |

---

## Files Created / Modified

| File | Action |
|------|--------|
| `docker-compose.yml` | Modified - added dbt volume mount |
| `airflow/dags/ingest_minio_dag.py` | Created - ingestion pipeline DAG |
| `infra/airflow/requirements.txt` | Modified - added dbt-postgres |
| `dbt/dbt_project.yml` | Created - dbt project config |
| `dbt/profiles.yml` | Created - PostgreSQL connection profile |
| `dbt/models/sources.yml` | Created - source definitions |
| `dbt/models/staging/stg_llm_generations.sql` | Created - staging model |
| `dbt/models/staging/stg_llm_traces.sql` | Created - staging model |
| `dbt/models/staging/stg_system_logs.sql` | Created - staging model |
| `dbt/models/staging/stg_system_traces.sql` | Created - staging model |
| `dbt/models/staging/stg_system_metrics.sql` | Created - staging model |
| `docs/report.md` | Modified - added Day 2 summary |
