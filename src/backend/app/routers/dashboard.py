from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ..agents.row_security import apply_row_security
from ..config import Settings
from ..connectors.clickhouse import ClickHouseConnector
from ..connectors.datahub import DataHubConnector
from ..connectors.metabase import MetabaseConnector
from ..connectors.postgres import PostgresConnector
from ..security import Principal, principal_from_scope


OVERVIEW_TABLES = [
    {"tableName": "gold.gold_llm_usage_daily", "columns": [{"name": "source_app"}]},
    {"tableName": "gold.gold_trace_health_daily", "columns": [{"name": "source_app"}]},
    {"tableName": "gold.gold_ingestion_health_daily", "columns": [{"name": "source_app"}]},
    {
        "tableName": "gold.gold_system_observability_daily",
        "columns": [{"name": "source_app"}, {"name": "service_name"}],
    },
]


def build_router(
    settings: Settings,
    clickhouse: ClickHouseConnector,
    metadata: DataHubConnector,
    postgres: PostgresConnector,
    metabase: MetabaseConnector,
) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

    @router.get("/overview")
    async def overview(request: Request) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        llm = await _one(clickhouse, """
            SELECT sum(generation_count) AS generation_count,
                   sum(total_tokens) AS total_tokens,
                   round(sum(total_cost_usd), 6) AS total_cost_usd,
                   sum(error_count) AS llm_errors
            FROM gold.gold_llm_usage_daily
            WHERE metric_date >= today() - 30
        """, principal)
        traces = await _one(clickhouse, """
            SELECT sum(trace_count) AS trace_count,
                   sum(error_count) AS trace_errors,
                   round(avg(p95_latency_ms), 2) AS p95_latency_ms
            FROM gold.gold_trace_health_daily
            WHERE metric_date >= today() - 30
        """, principal)
        ingestion = await _one(clickhouse, """
            SELECT sum(batch_count) AS batch_count,
                   sum(loaded_batch_count) AS loaded_batch_count,
                   sum(failed_batch_count) AS failed_batch_count,
                   sum(loaded_row_count) AS loaded_row_count
            FROM gold.gold_ingestion_health_daily
            WHERE metric_date >= today() - 30
        """, principal)
        services = await _query(clickhouse, """
            SELECT service_name,
                   sum(error_log_count) AS error_log_count,
                   sum(error_span_count) AS error_span_count,
                   round(avg(p95_span_duration_ms), 2) AS p95_span_duration_ms
            FROM gold.gold_system_observability_daily
            WHERE metric_date >= today() - 30
            GROUP BY service_name
            ORDER BY error_span_count DESC, error_log_count DESC
            LIMIT 10
        """, principal)
        llm_daily = await _query(clickhouse, """
            SELECT metric_date,
                   sum(generation_count) AS generation_count,
                   sum(total_tokens) AS total_tokens,
                   round(sum(total_cost_usd), 6) AS total_cost_usd,
                   sum(error_count) AS error_count
            FROM gold.gold_llm_usage_daily
            WHERE metric_date >= today() - 30
            GROUP BY metric_date ORDER BY metric_date
        """, principal)
        trace_daily = await _query(clickhouse, """
            SELECT metric_date,
                   sum(trace_count) AS trace_count,
                   sum(success_count) AS success_count,
                   sum(error_count) AS error_count,
                   round(avg(p95_latency_ms), 2) AS p95_latency_ms
            FROM gold.gold_trace_health_daily
            WHERE metric_date >= today() - 30
            GROUP BY metric_date ORDER BY metric_date
        """, principal)
        ingestion_daily = await _query(clickhouse, """
            SELECT metric_date,
                   sum(batch_count) AS batch_count,
                   sum(loaded_batch_count) AS loaded_batch_count,
                   sum(failed_batch_count) AS failed_batch_count,
                   sum(quarantined_batch_count) AS quarantined_batch_count,
                   sum(loaded_row_count) AS loaded_row_count
            FROM gold.gold_ingestion_health_daily
            WHERE metric_date >= today() - 30
            GROUP BY metric_date ORDER BY metric_date
        """, principal)
        model_cost = await _query(clickhouse, """
            SELECT model,
                   sum(total_tokens) AS total_tokens,
                   round(sum(total_cost_usd), 6) AS total_cost_usd,
                   sum(generation_count) AS generation_count,
                   sum(error_count) AS error_count
            FROM gold.gold_llm_usage_daily
            WHERE metric_date >= today() - 30
            GROUP BY model
            ORDER BY total_cost_usd DESC, total_tokens DESC
            LIMIT 10
        """, principal)
        agent_cost = await postgres.agent_cost_summary(
            user_id=None if principal.is_admin else principal.user_id,
            days=30,
        )
        agent_daily = agent_cost["daily"]
        today_cost = sum(
            float(row.get("estimated_cost_usd") or 0)
            for row in agent_daily
            if str(row.get("metric_date"))[:10] == datetime.now(timezone.utc).date().isoformat()
        )
        budget = settings.agent_daily_budget_usd
        result = {
            "periodDays": 30,
            "metrics": {**llm, **traces, **ingestion},
            "services": services,
            "series": {
                "llmDaily": llm_daily,
                "traceDaily": trace_daily,
                "ingestionDaily": ingestion_daily,
                "modelCost": model_cost,
                "agentCostDaily": agent_daily,
            },
            "agentCost": {
                "todayCostUsd": round(today_cost, 8),
                "dailyBudgetUsd": budget,
                "overBudget": today_cost > budget,
                "topQueries": agent_cost["topQueries"],
            },
            "alerts": ([{
                "type": "agent_cost_budget",
                "severity": "critical",
                "message": f"Agent cost today is ${today_cost:.6f}, above the ${budget:.2f} daily budget.",
            }] if today_cost > budget else []),
            "metadata": await metadata.health(),
            "queryId": query_id,
        }
        await _audit_dashboard_read(postgres, query_id, "/api/dashboard/overview", len(services), started, principal)
        return result

    @router.get("/data-quality")
    async def data_quality(request: Request, limit: int = 100) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        rows = await clickhouse.query(f"""
            SELECT test_name, table_name, status, actual_value,
                   threshold_value, executed_at
            FROM vsf_mart.data_quality_results
            ORDER BY executed_at DESC, test_name
            LIMIT {min(max(limit, 1), 500)}
        """)
        checks = [_quality_check(row) for row in rows]
        latest_at = max((str(row.get("executed_at") or "") for row in rows), default="")
        latest_checks = [check for check in checks if str(check["checkedAt"]) == latest_at]
        active_checks = latest_checks or checks
        passed = sum(check["status"] == "pass" for check in active_checks)
        failed = sum(check["status"] in {"fail", "error"} for check in active_checks)
        warned = sum(check["status"] == "warn" for check in active_checks)
        result = {
            "status": "ready" if rows else "new",
            "latestRun": ({"runId": latest_at or "server-quality", "checkedAt": latest_at} if rows else None),
            "summary": {
                "status": "pass" if failed == 0 else "fail",
                "totalChecks": len(active_checks),
                "passedCount": passed,
                "failedCount": failed,
                "warnedCount": warned,
                "passRate": round(passed / len(active_checks) * 100, 2) if active_checks else 0,
            },
            "byTable": _quality_by_table(active_checks),
            "recentChecks": checks,
            "source": "vsf_mart.data_quality_results",
            "queryId": query_id,
        }
        await _audit_dashboard_read(postgres, query_id, "/api/dashboard/data-quality", len(checks), started, principal)
        return result

    @router.get("/metabase-dashboards")
    async def metabase_dashboards(request: Request) -> dict[str, Any]:
        query_id = uuid4().hex
        started = perf_counter()
        principal = principal_from_scope(request.scope)
        if not principal.is_admin:
            raise HTTPException(status_code=403, detail="Curated Metabase embeds are restricted to administrators")
        dashboards = await metabase.dashboards()
        await _audit_dashboard_read(postgres, query_id, "/api/dashboard/metabase-dashboards", len(dashboards), started, principal)
        return {"dashboards": dashboards, "metabaseUrl": settings.metabase_public_url, "queryId": query_id}

    return router


async def _one(clickhouse: ClickHouseConnector, sql: str, principal: Principal) -> dict[str, Any]:
    rows = await _query(clickhouse, sql, principal)
    return rows[0] if rows else {}


async def _query(clickhouse: ClickHouseConnector, sql: str, principal: Principal) -> list[dict[str, Any]]:
    secured = apply_row_security(sql, dialect="clickhouse", tables=OVERVIEW_TABLES, principal=principal)
    return await clickhouse.query(secured)


async def _audit_dashboard_read(
    postgres: PostgresConnector,
    query_id: str,
    endpoint: str,
    row_count: int,
    started: float,
    principal: Principal,
) -> None:
    await postgres.audit({
        "query_id": query_id,
        "endpoint": endpoint,
        "request_type": "dashboard_read",
        "question": endpoint,
        "target": "mixed",
        "status": "success",
        "row_count": row_count,
        "execution_time_ms": round((perf_counter() - started) * 1000),
        "user_id": principal.user_id,
        "username": principal.username,
        "user_role": principal.role,
        "user_team": principal.team,
    })


def _quality_check(row: dict[str, Any]) -> dict[str, Any]:
    raw_status = str(row.get("status") or "unknown").lower()
    status = "pass" if raw_status in {"pass", "passed", "success"} else raw_status
    return {
        "runId": str(row.get("executed_at") or "server-quality"),
        "tableName": str(row.get("table_name") or "unknown"),
        "layer": "vsf_mart",
        "checkName": str(row.get("test_name") or "quality_check"),
        "checkType": "server_quality_gate",
        "status": status,
        "severity": "error",
        "observedValue": str(row.get("actual_value") or 0),
        "expectedValue": str(row.get("threshold_value") or 0),
        "failedRows": 0 if status == "pass" else 1,
        "checkedAt": row.get("executed_at"),
    }


def _quality_by_table(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for check in checks:
        key = str(check["tableName"])
        item = grouped.setdefault(key, {
            "tableName": key,
            "layer": check["layer"],
            "checkCount": 0,
            "passedCount": 0,
            "failedCount": 0,
            "warnedCount": 0,
            "failedRows": 0,
            "lastCheckedAt": check["checkedAt"],
        })
        item["checkCount"] += 1
        item["failedRows"] += check["failedRows"]
        if check["status"] == "pass":
            item["passedCount"] += 1
        elif check["status"] == "warn":
            item["warnedCount"] += 1
        else:
            item["failedCount"] += 1
    for item in grouped.values():
        item["status"] = "fail" if item["failedCount"] else ("warn" if item["warnedCount"] else "pass")
    return sorted(grouped.values(), key=lambda item: item["tableName"])
