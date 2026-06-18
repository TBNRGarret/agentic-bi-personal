# Observation Service Telemetry Plan

This service implements the Sprint 2 backend shell for an AI app that can call a
safe terminal API while producing logs and traces.

## Runtime Contracts

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Service status and observability configuration flags |
| `POST /chat` | AI-style terminal agent. It maps a natural-language request to an allowlisted command and records a Langfuse generation. |
| `POST /terminal/run` | Direct terminal API for safe commands. It uses `subprocess.run(..., shell=False)` and rejects commands outside `OBSERVATION_TERMINAL_ALLOWLIST`. |
| `POST /query` | Read-only ClickHouse query endpoint for `SELECT`, `SHOW`, and `DESCRIBE`. |
| `GET /schema` | Live ClickHouse schema snapshot for `vsf_raw`, `vsf_stg`, and `vsf_mart`. |

## Observability

Langfuse:

- Configure `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY`.
- Each `/chat` request creates a `terminal_chat` trace and a `terminal_command_plan` generation.
- The generation stores prompt text, deterministic planner output, usage estimates, session/user metadata, and status.

SigNoz:

- Configure `OTEL_EXPORTER_OTLP_ENDPOINT`, usually `http://signoz-otel-collector:4317` inside Docker.
- FastAPI requests and custom spans are exported through OTLP.
- JSON logs are emitted to stdout with `trace_id` and `span_id` when available, so a container log collector can correlate logs with traces.

## Local Smoke

```bash
docker compose build observation-service
docker compose up -d clickhouse observation-service
curl -s http://localhost:18000/health
curl -s http://localhost:18000/chat \
  -H 'content-type: application/json' \
  -d '{"message":"kiểm tra git status","user_id":"demo","session_id":"local-smoke"}'
```

Use `.env.observation.example` as the checklist for Langfuse and SigNoz values.
Use `docs/OBSERVATION_SERVICE_AUDIT.md` for the paired security and telemetry
audit checklist.
