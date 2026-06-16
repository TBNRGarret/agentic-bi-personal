from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import TerminalAgent
from .config import Settings
from .observability import LangfuseRecorder, configure_logging, configure_tracing, get_tracer, instrument_fastapi
from .sql_guard import SqlRejected, validate_readonly_sql
from .terminal import TerminalCommandRejected, TerminalRunner


settings = Settings.from_env()
logger = configure_logging(settings)
configure_tracing(settings, logger)

app = FastAPI(
    title="Agentic BI Observation Service",
    version="0.1.0",
    description="AI chat service that calls a safe terminal API and emits Langfuse/SigNoz telemetry.",
)
instrument_fastapi(app, logger)

terminal_runner = TerminalRunner(
    repo_root=settings.repo_root,
    allowlist=settings.terminal_allowlist,
    timeout_seconds=settings.terminal_timeout_seconds,
    max_output_chars=settings.terminal_max_output_chars,
)
langfuse = LangfuseRecorder(settings, logger)
terminal_agent = TerminalAgent(settings=settings, terminal=terminal_runner, langfuse=langfuse, logger=logger)
tracer = get_tracer("agentic_bi.observation.api")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    user_id: str | None = None
    session_id: str | None = None


class TerminalRunRequest(BaseModel):
    command: str = Field(..., min_length=1)
    cwd: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        "source_app_id": settings.source_app_id,
        "langfuse_configured": bool(settings.langfuse_public_key and settings.langfuse_secret_key),
        "otlp_configured": bool(settings.otlp_endpoint),
    }


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    return terminal_agent.answer(request.message, user_id=request.user_id, session_id=request.session_id)


@app.post("/terminal/run")
def terminal_run(request: TerminalRunRequest) -> dict[str, Any]:
    with tracer.start_as_current_span("terminal_api.run") as span:
        span.set_attribute("terminal.command", request.command)
        try:
            result = terminal_runner.run(request.command, cwd=request.cwd, timeout_seconds=request.timeout_seconds)
        except TerminalCommandRejected as error:
            span.record_exception(error)
            raise HTTPException(status_code=400, detail=str(error)) from error
        logger.info(
            "terminal_api_completed",
            extra={
                "extra": {
                    "source_app_id": settings.source_app_id,
                    "command": request.command,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                }
            },
        )
        return result.to_dict()


@app.get("/schema")
def schema() -> dict[str, Any]:
    sql = """
    SELECT database, table, name, type
    FROM system.columns
    WHERE database IN ('vsf_raw', 'vsf_stg', 'vsf_mart')
    ORDER BY database, table, position
    """
    try:
        rows = _run_clickhouse_query(sql)
    except Exception as error:
        return {
            "metadata_source": "clickhouse",
            "datahub_gms_url": settings.datahub_gms_url,
            "available": False,
            "error": str(error),
            "schemas": [],
        }
    schemas: dict[str, dict[str, list[dict[str, str]]]] = {}
    for row in rows["rows"]:
        schemas.setdefault(row["database"], {}).setdefault(row["table"], []).append(
            {"name": row["name"], "type": row["type"]}
        )
    return {
        "metadata_source": "clickhouse",
        "datahub_gms_url": settings.datahub_gms_url,
        "available": True,
        "schemas": schemas,
    }


@app.post("/query")
def query(request: QueryRequest) -> dict[str, Any]:
    try:
        validate_readonly_sql(request.sql)
    except SqlRejected as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _run_clickhouse_query(request.sql)


def _run_clickhouse_query(sql: str) -> dict[str, Any]:
    try:
        import clickhouse_connect
    except Exception as error:
        raise RuntimeError("clickhouse-connect is not installed in this runtime.") from error

    started = time.monotonic()
    try:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            secure=settings.clickhouse_secure,
        )
        result = client.query(sql, settings={"max_execution_time": settings.query_timeout_seconds})
    except Exception as error:
        logger.exception("clickhouse_query_failed", extra={"extra": {"sql": sql}})
        raise HTTPException(status_code=502, detail=str(error)) from error
    duration_ms = int((time.monotonic() - started) * 1000)
    columns = list(result.column_names)
    rows = [dict(zip(columns, row)) for row in result.result_rows]
    logger.info(
        "clickhouse_query_completed",
        extra={"extra": {"row_count": len(rows), "duration_ms": duration_ms}},
    )
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "duration_ms": duration_ms,
    }
