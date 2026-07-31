from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents.dashboard_generator import generate_dashboard
from ..agents.model2 import HybridText2SQLOrchestrator
from ..agents.monitoring_agent import MonitoringAgent
from ..agents.multi_agent import MultiAgentOrchestrator
from ..agents.sql_safety import SqlSafetyError, validate_readonly_sql
from ..agents.row_security import RowSecurityError, apply_row_security, visible_tables
from ..config import Settings
from ..connectors.clickhouse import ClickHouseConnector
from ..connectors.datahub import DataHubConnector
from ..connectors.postgres import PostgresConnector
from ..security import Principal, principal_from_scope
from ..observability import current_trace_context
from ..services.context_manager import ContextManager


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    target: str = "auto"
    model: Literal["model1", "model2"] = "model1"
    limit: int = Field(default=100, ge=1, le=500)
    conversation: list[dict[str, Any]] = Field(default_factory=list)
    includeSynthesis: bool = True
    sessionId: str | None = Field(default=None, max_length=64)


class SqlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=8000)
    target: str = "clickhouse"
    limit: int = Field(default=100, ge=1, le=500)


class DashboardRequest(ChatRequest):
    max_panels: int = Field(default=3, ge=2, le=4)


def build_router(
    settings: Settings,
    clickhouse: ClickHouseConnector,
    metadata: DataHubConnector,
    postgres: PostgresConnector,
    context: ContextManager,
) -> APIRouter:
    router = APIRouter()
    agent = MonitoringAgent(settings, clickhouse, metadata, postgres)
    orchestrator = MultiAgentOrchestrator(settings, agent)
    model2_orchestrator = HybridText2SQLOrchestrator(settings, clickhouse, metadata, postgres)

    async def run_chat(
        payload: ChatRequest, endpoint: str, principal: Principal
    ) -> dict[str, Any]:
        query_id = uuid4().hex
        session_id = payload.sessionId
        if not session_id:
            session = await context.create_session(principal, payload.question[:160])
            session_id = str(session["session_id"])
        try:
            memory, server_conversation = await context.load_context(
                principal, session_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        request_memory = memory
        active_orchestrator = model2_orchestrator if payload.model == "model2" else orchestrator
        result = await active_orchestrator.answer(
            payload.question,
            target=payload.target,
            limit=payload.limit,
            conversation=server_conversation or payload.conversation,
            memory=request_memory,
            principal=principal,
            include_synthesis=payload.includeSynthesis,
        )
        result["queryId"] = query_id
        await context.persist_turn(
            principal=principal,
            session_id=session_id,
            question=payload.question,
            result=result,
            previous_memory=request_memory,
        )
        await _audit_result(
            postgres, query_id, endpoint, "chat", payload, result, principal
        )
        return result

    @router.post("/chat")
    @router.post("/api/agent/chat")
    async def chat_alias(request: Request, payload: ChatRequest) -> dict[str, Any]:
        return await run_chat(
            payload, "/chat", principal_from_scope(request.scope)
        )

    @router.post("/api/query/chat")
    async def chat(request: Request, payload: ChatRequest) -> dict[str, Any]:
        return await run_chat(
            payload, "/api/query/chat", principal_from_scope(request.scope)
        )

    @router.post("/api/query/chat/stream")
    @router.post("/api/monitoring/chat/stream")
    async def chat_stream(
        request: Request, payload: ChatRequest
    ) -> StreamingResponse:
        query_id = uuid4().hex
        principal = principal_from_scope(request.scope)
        session_id = payload.sessionId
        if not session_id:
            session = await context.create_session(principal, payload.question[:160])
            session_id = str(session["session_id"])
        try:
            memory, server_conversation = await context.load_context(
                principal, session_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        request_memory = memory
        return StreamingResponse(
            _stream_chat_events(
                model2_orchestrator if payload.model == "model2" else orchestrator,
                postgres,
                payload,
                query_id,
                principal=principal,
                context=context,
                session_id=session_id,
                memory=request_memory,
                conversation=server_conversation or payload.conversation,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Query-ID": query_id},
        )

    @router.post("/query")
    @router.post("/api/agent/sql")
    @router.post("/api/query/dwh")
    async def query(request: Request, payload: SqlRequest) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        if payload.target.lower() in {"dwh", "postgres", "postgresql"} and not principal.is_admin:
            raise HTTPException(
                status_code=403, detail="Operational PostgreSQL requires admin"
            )
        try:
            target, sql = await _prepare_sql_query(
                settings=settings,
                clickhouse=clickhouse,
                postgres=postgres,
                principal=principal,
                target=payload.target,
                sql=payload.sql,
                limit=payload.limit,
            )
        except (SqlSafetyError, RowSecurityError) as exc:
            await postgres.audit(
                {
                    "query_id": query_id,
                    "endpoint": "/query",
                    "request_type": "sql",
                    "question": payload.sql,
                    "generated_sql": payload.sql,
                    "target": payload.target,
                    "status": "blocked",
                    "execution_time_ms": round(
                        (perf_counter() - started) * 1000
                    ),
                    "error_code": "POLICY_BLOCKED",
                    "error_message": str(exc),
                    **_identity(principal),
                }
            )
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        try:
            if target == "dwh":
                rows = await postgres.query(sql)
            else:
                rows = await clickhouse.query(sql)
        except Exception as exc:
            await postgres.audit(
                {
                    "query_id": query_id,
                    "endpoint": "/query",
                    "request_type": "sql",
                    "question": payload.sql,
                    "generated_sql": sql,
                    "target": payload.target,
                    "status": "error",
                    "execution_time_ms": round(
                        (perf_counter() - started) * 1000
                    ),
                    "error_code": "QUERY_ERROR",
                    "error_message": str(exc),
                    **_identity(principal),
                }
            )
            raise HTTPException(
                status_code=400, detail={"error": "Query execution failed"}
            ) from exc
        elapsed = round((perf_counter() - started) * 1000)
        await postgres.audit(
            {
                "query_id": query_id,
                "endpoint": "/query",
                "request_type": "sql",
                "question": payload.sql,
                "generated_sql": sql,
                "target": target,
                "status": "success",
                "row_count": len(rows),
                "execution_time_ms": elapsed,
                **_identity(principal),
            }
        )
        return {
            "success": True,
            "target": target,
            "rowCount": len(rows),
            "rows": rows[: payload.limit],
            "executionTimeMs": elapsed,
            "queryId": query_id,
        }

    @router.post("/api/query/explain")
    async def explain(request: Request, payload: SqlRequest) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        if payload.target.lower() in {"dwh", "postgres", "postgresql"} and not principal.is_admin:
            raise HTTPException(
                status_code=403, detail="Operational PostgreSQL requires admin"
            )
        try:
            _, sql = await _prepare_sql_query(
                settings=settings,
                clickhouse=clickhouse,
                postgres=postgres,
                principal=principal,
                target=payload.target,
                sql=payload.sql,
                limit=payload.limit,
            )
        except (SqlSafetyError, RowSecurityError) as exc:
            await postgres.audit(
                {
                    "query_id": query_id,
                    "endpoint": "/api/query/explain",
                    "request_type": "explain",
                    "question": payload.sql,
                    "generated_sql": payload.sql,
                    "target": payload.target,
                    "status": "blocked",
                    "execution_time_ms": round((perf_counter() - started) * 1000),
                    "error_code": "POLICY_BLOCKED",
                    "error_message": str(exc),
                    **_identity(principal),
                }
            )
            raise HTTPException(status_code=403, detail={"error": str(exc)}) from exc
        result = {
            "success": True,
            "sql": sql,
            "tables": _sql_tables(sql),
            "explanation": (
                "Read-only query validated. It reads the listed tables and "
                "returns at most the configured row limit."
            ),
            "explanationSource": "rule_based",
            "warnings": [],
            "profile": {"readonly": True, "maxRows": payload.limit},
            "executionTimeMs": round((perf_counter() - started) * 1000),
            "estimatedCostUsd": 0,
            "queryId": query_id,
        }
        await postgres.audit(
            {
                "query_id": query_id,
                "endpoint": "/api/query/explain",
                "request_type": "explain",
                "question": payload.sql,
                "generated_sql": sql,
                "target": payload.target,
                "status": "success",
                "execution_time_ms": result["executionTimeMs"],
                "selected_tables": result["tables"],
                **_identity(principal),
            }
        )
        return result

    @router.get("/schema")
    @router.get("/api/agent/schema")
    @router.get("/api/query/schema")
    async def schema(
        request: Request, datasource: str = "all", refresh: bool = False
    ) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        normalized_datasource = datasource.lower()
        clickhouse_schemas = set(settings.clickhouse_analytics_schema_list)
        if normalized_datasource in {"dwh", "postgres", "postgresql"}:
            tables = await postgres.describe_tables(("raw", "ops"))
            source = "postgres_schema"
        elif normalized_datasource == "clickhouse":
            tables = await metadata.list_tables(refresh=refresh)
            source = "datahub"
        else:
            clickhouse_tables, postgres_tables = await asyncio.gather(
                metadata.list_tables(refresh=refresh),
                postgres.describe_tables(("raw", "ops")),
            )
            tables = _merge_tables(clickhouse_tables, postgres_tables)
            source = "datahub+postgres_schema"
        if normalized_datasource == "clickhouse":
            tables = [
                table
                for table in tables
                if table.get("database") in clickhouse_schemas
            ]
        tables = visible_tables(tables, principal)
        result = {
            "datasource": datasource,
            "source": source,
            "tableCount": len(tables),
            "tables": tables,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "queryId": query_id,
        }
        await postgres.audit(
            {
                "query_id": query_id,
                "endpoint": "/schema",
                "request_type": "schema",
                "question": f"datasource={datasource}; refresh={refresh}",
                "target": datasource,
                "status": "success",
                "row_count": len(tables),
                "execution_time_ms": round((perf_counter() - started) * 1000),
                "metadata": {"source": source},
                **_identity(principal),
            }
        )
        return result

    @router.get("/api/query/tables")
    async def tables(request: Request) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        clickhouse_tables, postgres_tables = await asyncio.gather(
            metadata.list_tables(),
            postgres.describe_tables(("raw", "ops")),
        )
        tables = visible_tables(
            _merge_tables(clickhouse_tables, postgres_tables), principal
        )
        await postgres.audit(
            {
                "query_id": query_id,
                "endpoint": "/api/query/tables",
                "request_type": "schema",
                "question": "list queryable tables",
                "target": "all",
                "status": "success",
                "row_count": len(tables),
                "execution_time_ms": round((perf_counter() - started) * 1000),
                **_identity(principal),
            }
        )
        return {"tables": tables, "queryId": query_id}

    @router.post("/api/query/dashboard")
    async def dashboard(
        request: Request, payload: DashboardRequest
    ) -> dict[str, Any]:
        query_id = uuid4().hex
        principal = principal_from_scope(request.scope)
        try:
            memory, conversation = await context.load_context(
                principal, payload.sessionId
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result = await generate_dashboard(
            agent,
            payload.question,
            target=payload.target,
            limit=payload.limit,
            max_panels=payload.max_panels,
            conversation=conversation or payload.conversation,
            memory=memory,
            principal=principal,
            include_synthesis=payload.includeSynthesis,
        )
        result["queryId"] = query_id
        await postgres.audit(
            {
                "query_id": query_id,
                "endpoint": "/api/query/dashboard",
                "request_type": "dashboard",
                "question": payload.question,
                "generated_sql": "\n\n".join(
                    str(panel.get("sql") or "") for panel in result["panels"]
                ),
                "target": payload.target,
                "status": result["status"],
                "row_count": sum(
                    int(panel.get("rowCount") or 0) for panel in result["panels"]
                ),
                "selected_tables": list(
                    {
                        table
                        for panel in result["panels"]
                        for table in panel.get("selectedTables") or []
                    }
                ),
                "metadata": {"panelCount": result["panelCount"]},
                **_identity(principal),
            }
        )
        return result

    return router


async def _stream_chat_events(
    agent: Any,
    postgres: PostgresConnector,
    payload: ChatRequest,
    query_id: str,
    *,
    principal: Principal | None = None,
    context: ContextManager | None = None,
    session_id: str | None = None,
    memory: dict[str, Any] | None = None,
    conversation: list[dict[str, Any]] | None = None,
):
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def produce() -> None:
        active_principal = principal or Principal(
            user_id="system",
            username="system",
            role="admin",
            team="platform",
        )
        try:
            result = await agent.answer(
                payload.question,
                target=payload.target,
                limit=payload.limit,
                conversation=conversation or payload.conversation,
                memory=memory,
                principal=active_principal,
                include_synthesis=payload.includeSynthesis,
                event_sink=queue.put_nowait,
            )
        except Exception as exc:
            result = {
                "agent": "metadata_sql_agent",
                "question": payload.question,
                "target": payload.target,
                "status": "error",
                "answer": "The agent request failed before completion.",
                "error": str(exc),
                "rows": [],
                "rowCount": 0,
            }
        result["queryId"] = query_id
        if context and session_id:
            await context.persist_turn(
                principal=active_principal,
                session_id=session_id,
                question=payload.question,
                result=result,
                previous_memory=memory or {},
            )
        synthesis = result.get("llmSynthesis") or {}
        if not synthesis.get("streamed"):
            for chunk in _chunks(str(result.get("answer") or "")):
                await queue.put({"type": "delta", "text": chunk})
                await asyncio.sleep(0)
        await _audit_result(
            postgres,
            query_id,
            "/api/query/chat/stream",
            "chat_stream",
            payload,
            result,
            active_principal,
        )
        await queue.put({"type": "final", "payload": result})
        await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
            except (asyncio.TimeoutError, TimeoutError):
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield _sse(event)
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


async def _audit_result(
    postgres: PostgresConnector,
    query_id: str,
    endpoint: str,
    request_type: str,
    payload: ChatRequest,
    result: dict[str, Any],
    principal: Principal | None = None,
) -> None:
    otel_trace_id, otel_span_id = current_trace_context()
    audit_metadata = {
        "metadataContext": result.get("metadataContext"),
        "conversationLength": len(payload.conversation),
        "model": getattr(payload, "model", "model1"),
    }
    if otel_trace_id:
        audit_metadata["trace_id"] = otel_trace_id
        audit_metadata["otel_trace_id"] = otel_trace_id
    if otel_span_id:
        audit_metadata["span_id"] = otel_span_id
        audit_metadata["otel_span_id"] = otel_span_id

    await postgres.audit(
        {
            "query_id": query_id,
            "endpoint": endpoint,
            "request_type": request_type,
            "question": payload.question,
            "generated_sql": result.get("sql"),
            "target": result.get("target"),
            "status": result.get("status"),
            "row_count": result.get("rowCount"),
            "execution_time_ms": result.get("executionTimeMs"),
            "selected_tables": result.get("selectedTables"),
            "attempts": result.get("attempts"),
            "token_usage": result.get("llmUsage"),
            "error_code": "AGENT_QUERY_ERROR"
            if result.get("status") == "error"
            else None,
            "error_message": result.get("error"),
            "metadata": audit_metadata,
            **(_identity(principal) if principal else {}),
        }
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _chunks(value: str) -> list[str]:
    words = value.split()
    return [
        " ".join(words[index : index + 6]) + (" " if index + 6 < len(words) else "")
        for index in range(0, len(words), 6)
    ]


def _sql_tables(sql: str) -> list[str]:
    import re

    return [
        match.group(1)
        for match in re.finditer(
            r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)", sql, re.IGNORECASE
        )
    ]


def _merge_tables(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for table in group:
            name = str(table.get("tableName") or "")
            if name:
                merged[name] = table
    return sorted(merged.values(), key=lambda table: str(table.get("tableName") or ""))


def _identity(principal: Principal) -> dict[str, str]:
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "user_role": principal.role,
        "user_team": principal.team,
    }


async def _prepare_sql_query(
    *,
    settings: Settings,
    clickhouse: ClickHouseConnector,
    postgres: PostgresConnector,
    principal: Principal,
    target: str,
    sql: str,
    limit: int,
) -> tuple[str, str]:
    normalized_target = target.lower()
    if normalized_target in {"dwh", "postgres", "postgresql"}:
        policy_tables = await postgres.describe_tables(("raw", "ops"))
        dialect = "postgres"
        resolved_target = "dwh"
    else:
        policy_tables = await clickhouse.describe_tables(
            tuple(settings.clickhouse_analytics_schema_list)
        )
        dialect = "clickhouse"
        resolved_target = "clickhouse"

    validated_sql = validate_readonly_sql(sql, limit)
    secured_sql = apply_row_security(
        validated_sql,
        dialect=dialect,
        tables=policy_tables,
        principal=principal,
    )
    return resolved_target, validate_readonly_sql(secured_sql, limit)
