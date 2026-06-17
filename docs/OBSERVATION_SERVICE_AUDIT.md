# Observation Service Audit Plan

Use this audit after changing `src/backend`.

## Security Checks

- Terminal commands run with `shell=False`.
- Commands outside `OBSERVATION_TERMINAL_ALLOWLIST` are rejected.
- Shell control syntax is rejected, including `&&`, `||`, `|`, `;`, redirection, backticks, and `$()`.
- `cwd` cannot escape `OBSERVATION_REPO_ROOT`.
- `.env` values and Langfuse keys are not logged or returned in API responses.
- `/query` accepts only one read-only `SELECT`, `SHOW`, or `DESCRIBE` statement.

## Telemetry Checks

- `/chat` creates a Langfuse trace named `terminal_chat` and generation named `terminal_command_plan` when Langfuse keys are configured.
- FastAPI instrumentation and custom spans are exported to `OTEL_EXPORTER_OTLP_ENDPOINT`.
- JSON logs include `trace_id` and `span_id` when an OpenTelemetry span is active.
- Command logs include command, duration, exit code, and source app id.
- Query logs include row count and duration.

## Required Evidence

```bash
python -m py_compile src/backend/app/*.py
pytest tests/test_observation_service_contracts.py tests/test_observation_service_runtime_contracts.py -q
```

Optional runtime smoke with dependencies installed:

```bash
cd src/backend
OBSERVATION_REPO_ROOT=/workspaces/agentic-bi uvicorn app.main:app --port 18000
curl -s http://localhost:18000/health
curl -s http://localhost:18000/chat \
  -H 'content-type: application/json' \
  -d '{"message":"kiểm tra git status","user_id":"demo","session_id":"local-smoke"}'
curl -s http://localhost:18000/terminal/run \
  -H 'content-type: application/json' \
  -d '{"command":"sh -c pwd"}'
```

## Audit Prompt

```text
Bạn là auditor cho Observation Service trong repo /workspaces/agentic-bi. Review như security + observability code review.

Ưu tiên findings có file/line. Kiểm:
- terminal execution dùng shell=False, allowlist đúng, reject shell control syntax, không path traversal qua cwd, có timeout/output limit.
- /query không cho write SQL hoặc multi-statement.
- Langfuse optional; khi bật có trace/generation với input/output/status/usage/user/session/source_app/env metadata.
- SigNoz/OpenTelemetry dùng service.name agentic-bi-observation-service và có custom spans cho /chat + /terminal/run.
- JSON logs parse được, không lộ secrets, có trace_id/span_id khi active span.
- Docker/env/docs đủ để chạy service ở localhost:18000.
- Tests bao phủ endpoint contracts, terminal safety, query guard, timeout, observability hooks.

Output:
1. Findings trước, theo severity, có path/line.
2. Test evidence đã chạy.
3. Residual risks và follow-up ngắn.
```
