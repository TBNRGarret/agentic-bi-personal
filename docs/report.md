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
