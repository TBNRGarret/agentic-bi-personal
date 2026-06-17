# Observation Service

FastAPI backend for Sprint 2. It exposes the plan contracts:

- `POST /chat` - small AI-style terminal agent that plans a safe command, calls `/terminal/run` behavior internally, and returns the result.
- `POST /terminal/run` - allowlisted terminal API. Commands run without a shell and stay inside the repository working directory.
- `POST /query` - read-only ClickHouse SQL endpoint.
- `GET /schema` - live ClickHouse schema snapshot for `vsf_raw`, `vsf_stg`, and `vsf_mart`.

Telemetry:

- Langfuse is optional. Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST`.
- SigNoz receives traces through OTLP. Set `OTEL_EXPORTER_OTLP_ENDPOINT`, for example `http://signoz-otel-collector:4317`.
- Logs are JSON on stdout and include trace/span IDs when an OpenTelemetry span is active.

Local run:

```bash
cd src/backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
OBSERVATION_REPO_ROOT=/workspaces/agentic-bi uvicorn app.main:app --reload --port 18000
```

Smoke request:

```bash
curl -s http://localhost:18000/chat \
  -H 'content-type: application/json' \
  -d '{"message":"kiểm tra git status"}'
```
