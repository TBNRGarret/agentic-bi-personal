from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import TerminalAgent
from .config import get_settings
from .connectors.clickhouse import ClickHouseConnector
from .connectors.datahub import DataHubConnector
from .connectors.metabase import MetabaseConnector
from .connectors.postgres import PostgresConnector
from .observability import (
    LangfuseRecorder,
    configure_logging,
    configure_tracing,
    get_tracer,
    instrument_fastapi,
)
from .routers.auth import build_router as build_auth_router
from .routers.dashboard import build_router as build_dashboard_router
from .routers.query import build_router as build_query_router
from .routers.sessions import build_router as build_sessions_router
from .security import (
    AuthGatewayMiddleware,
    AuthService,
    principal_from_scope,
)
from .services.context_manager import ContextManager
from .terminal import TerminalCommandRejected, TerminalRunner

settings = get_settings()
logger = configure_logging(settings)
configure_tracing(settings, logger)

clickhouse = ClickHouseConnector(settings)
postgres = PostgresConnector(settings)
datahub = DataHubConnector(settings, clickhouse)
metabase = MetabaseConnector(settings)
auth = AuthService(settings, postgres)
context = ContextManager(postgres)

terminal_runner = TerminalRunner(
    repo_root=settings.repo_root,
    allowlist=settings.terminal_allowlist,
    timeout_seconds=settings.terminal_timeout_seconds,
    max_output_chars=settings.terminal_max_output_chars,
)
langfuse = LangfuseRecorder(settings, logger)
terminal_agent = TerminalAgent(
    settings=settings,
    terminal=terminal_runner,
    langfuse=langfuse,
    logger=logger,
)
tracer = get_tracer("agentic_bi.observation.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await postgres.ensure_agentic_schema()
    await auth.ensure_bootstrap_user()
    yield


app = FastAPI(
    title="Agentic BI Observation Service",
    version="1.0.0",
    description=(
        "Server-compatible Agentic BI agents, dashboard APIs, safe SQL, "
        "terminal operations, and DataHub-backed metadata."
    ),
    lifespan=lifespan,
)
app.add_middleware(AuthGatewayMiddleware, auth=auth, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Query-ID", "Authorization"],
)
app.include_router(build_auth_router(auth))
app.include_router(build_sessions_router(context))
app.include_router(build_query_router(settings, clickhouse, datahub, postgres, context))
app.include_router(build_dashboard_router(settings, clickhouse, datahub, postgres, metabase))
instrument_fastapi(app, logger)


class TerminalChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None


class TerminalRunRequest(BaseModel):
    command: str = Field(..., min_length=1)
    cwd: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)


@app.get("/health")
async def health() -> dict[str, Any]:
    clickhouse_ok = await clickhouse.health()
    postgres_ok = await postgres.health()
    datahub_status = await datahub.health()
    metabase_ok = await metabase.health()
    ready = clickhouse_ok and postgres_ok
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "service": settings.service_name,
        "environment": settings.environment,
        "source_app_id": settings.source_app_id,
        "langfuse_configured": bool(settings.langfuse_public_key and settings.langfuse_secret_key),
        "otlp_configured": bool(settings.otlp_endpoint),
        "dependencies": {
            "clickhouse": "up" if clickhouse_ok else "down",
            "postgres": "up" if postgres_ok else "down",
            "datahub": datahub_status,
            "metabase": "up" if metabase_ok else "down",
            "llm": {
                "status": "configured" if settings.llm_configured else "optional",
                "provider": settings.llm_provider,
                "model": settings.llm_model,
            },
        },
    }


@app.post("/api/terminal/chat")
def terminal_chat(request: Request, payload: TerminalChatRequest) -> dict[str, Any]:
    principal = principal_from_scope(request.scope)
    return terminal_agent.answer(
        payload.message,
        user_id=principal.user_id,
        session_id=payload.session_id,
    )


@app.post("/api/terminal/run")
@app.post("/terminal/run")
def terminal_run(payload: TerminalRunRequest) -> dict[str, Any]:
    with tracer.start_as_current_span("terminal_api.run") as span:
        span.set_attribute("terminal.command", payload.command)
        try:
            result = terminal_runner.run(
                payload.command,
                cwd=payload.cwd,
                timeout_seconds=payload.timeout_seconds,
            )
        except TerminalCommandRejected as error:
            span.record_exception(error)
            raise HTTPException(status_code=400, detail=str(error)) from error
        return result.to_dict()
