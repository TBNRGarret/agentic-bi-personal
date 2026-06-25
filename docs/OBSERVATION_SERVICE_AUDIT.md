# Observation Service Audit

## Security Checks

- Confirm SQL is read-only, bounded by a row limit, and checked against known tables.
- Confirm terminal commands are allowlisted and run with `shell=False`.
- Confirm JWT secrets and bootstrap credentials are supplied through `.env.agentic-ui`.
- Confirm non-admin users receive row-level source and service filters.

## Telemetry Checks

- Confirm request trace IDs appear in structured logs.
- Confirm Langfuse is optional and disabled cleanly without credentials.
- Confirm the OTLP exporter reaches the server SigNoz collector when configured.
- Confirm agent query audit rows are written to `ops.agent_query_audit`.

## Audit Prompt

Review authentication, SQL guardrails, DataHub fallback behavior, ClickHouse timeouts, streaming termination, and whether the UI exposes secrets or unrestricted operational data.
