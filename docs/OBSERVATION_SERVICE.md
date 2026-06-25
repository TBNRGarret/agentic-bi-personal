# Observation Service

The server keeps its existing Airflow, DAG, dbt, DataHub, ClickHouse, MinIO, and Metabase flow. The Observation Service is an additive API layer that reads those services without replacing their orchestration.

## Interfaces

- `POST /chat`: legacy allowlisted terminal agent contract.
- `POST /terminal/run`: execute an allowlisted command without a shell.
- `GET /schema`: DataHub metadata with live ClickHouse fallback.
- `POST /query`: legacy read-only ClickHouse query contract.
- `POST /api/query/chat`: multi-agent analytics chat.
- `POST /api/query/chat/stream`: SSE agent progress and results.
- `POST /api/query/dashboard`: generated multi-panel dashboard.
- `GET /api/dashboard/overview`: operational dashboard data.
- `GET /api/dashboard/data-quality`: server quality-gate results.

Langfuse records optional LLM generations and OpenTelemetry exports traces to SigNoz when credentials and an OTLP endpoint are configured.

Start the additive UI stack with:

```bash
sudo docker compose -f docker-compose.agentic-ui.yml up -d --build
```
