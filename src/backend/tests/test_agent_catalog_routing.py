from __future__ import annotations

import asyncio
from typing import Any

from app.agents.chart_spec import infer_chart_spec
from app.agents.monitoring_agent import MonitoringAgent, _generate_sql, _plan_matches_intent
from app.agents.request_orchestrator import build_request_plan
from app.agents.multi_agent import MultiAgentOrchestrator
from app.agents.table_selector import TableSelector
from app.config import Settings
from app.security import Principal


OPS_AUDIT_TABLE = {
    "tableName": "ops.agent_query_audit",
    "database": "ops",
    "datasource": "dwh",
    "columns": [
        {"name": "query_id", "type": "text"},
        {"name": "status", "type": "text"},
        {"name": "created_at", "type": "timestamp"},
        {"name": "error_message", "type": "text"},
    ],
}

SERVER_MART_TABLE = {
    "tableName": "vsf_mart.gold_app_daily_cost",
    "database": "vsf_mart",
    "datasource": "clickhouse",
    "columns": [
        {"name": "date", "type": "Date"},
        {"name": "source_app_id", "type": "String"},
        {"name": "model", "type": "String"},
        {"name": "request_count", "type": "UInt64"},
        {"name": "total_tokens", "type": "UInt64"},
        {"name": "total_cost_usd", "type": "Float64"},
        {"name": "error_count", "type": "UInt64"},
        {"name": "avg_latency_ms", "type": "Float64"},
        {"name": "p95_latency_ms", "type": "Float64"},
    ],
}


GOLD_OBSERVABILITY_TABLE = {
    "tableName": "gold.gold_system_observability_daily",
    "database": "gold",
    "datasource": "clickhouse",
    "columns": [
        {"name": "metric_date", "type": "Date"},
        {"name": "source_app", "type": "String"},
        {"name": "service_name", "type": "String"},
        {"name": "log_count", "type": "UInt64"},
        {"name": "error_log_count", "type": "UInt64"},
        {"name": "span_count", "type": "UInt64"},
        {"name": "error_span_count", "type": "UInt64"},
        {"name": "avg_span_duration_ms", "type": "Float64"},
        {"name": "p95_span_duration_ms", "type": "Float64"},
    ],
}

SILVER_LOGS_TABLE = {
    "tableName": "silver.silver_system_logs",
    "database": "silver",
    "datasource": "clickhouse",
    "columns": [
        {"name": "event_time", "type": "DateTime"},
        {"name": "source_app", "type": "String"},
        {"name": "service_name", "type": "String"},
        {"name": "severity", "type": "String"},
        {"name": "message", "type": "String"},
        {"name": "trace_id", "type": "String"},
        {"name": "span_id", "type": "String"},
    ],
}


class FakeMetadata:
    source_name = "datahub"

    def __init__(self) -> None:
        self.calls = 0

    async def list_tables(self) -> list[dict[str, Any]]:
        self.calls += 1
        return [SERVER_MART_TABLE]


class FakeClickHouse:
    async def query(self, sql: str) -> list[dict[str, Any]]:
        return []

    async def describe_tables(self, schemas: tuple[str, ...]) -> list[dict[str, Any]]:
        return [SERVER_MART_TABLE]


class FakePostgres:
    def __init__(self) -> None:
        self.describe_calls: list[tuple[str, ...]] = []
        self.sql: list[str] = []

    async def describe_tables(self, schemas: tuple[str, ...]) -> list[dict[str, Any]]:
        self.describe_calls.append(schemas)
        return [OPS_AUDIT_TABLE]

    async def query(self, sql: str) -> list[dict[str, Any]]:
        self.sql.append(sql)
        return [
            {
                "query_id": "q-1",
                "status": "failed",
                "created_at": "2026-06-18T00:00:00Z",
                "error_message": "test failure",
            }
        ]


class FakePlannerlessLlm:
    async def classify_request_slots(self, question: str) -> dict[str, Any]:
        return {}


class FakeInsightPlanner:
    async def classify_request_slots(self, question: str) -> dict[str, Any]:
        return {}

    async def plan_agents(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "intent": "insight",
            "agents": ["insight_agent"],
            "confidence": 0.95,
            "rationale": "errors imply root-cause analysis",
        }


class FakeChartAgent:
    def __init__(self) -> None:
        self.clickhouse = FakeClickHouse()
        self.postgres = FakePostgres()
        self.metadata = FakeMetadata()

    async def answer(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "answer": "chart fallback",
            "target": "clickhouse",
            "rows": [{"metric_date": "2026-06-18", "request_count": 1}],
            "rowCount": 1,
            "chartSpec": {"type": "line", "x": "metric_date", "y": "request_count"},
            "selectedTables": [SERVER_MART_TABLE["tableName"]],
            "attempts": [],
            "llmUsage": {},
        }


def test_dwh_route_uses_postgres_schema_even_when_datahub_is_populated() -> None:
    async def run() -> None:
        settings = Settings(
            openrouter_api_key="",
            openai_api_key="",
            table_selector_embedding_enabled=False,
        )
        metadata = FakeMetadata()
        postgres = FakePostgres()
        agent = MonitoringAgent(
            settings,
            FakeClickHouse(),  # type: ignore[arg-type]
            metadata,  # type: ignore[arg-type]
            postgres,  # type: ignore[arg-type]
        )
        result = await agent.answer(
            "show recent failed query audit errors",
            target="dwh",
            principal=Principal(
                user_id="admin-1",
                username="admin",
                role="admin",
                team="platform",
            ),
            include_synthesis=False,
        )
        assert result["status"] == "success"
        assert result["target"] == "dwh"
        assert result["metadataContext"]["source"] == "postgres_schema"
        assert result["selectedTables"] == ["ops.agent_query_audit"]
        assert postgres.describe_calls == [("ops",)]
        assert metadata.calls == 0

    asyncio.run(run())


def test_table_selector_accepts_and_prefers_server_mart() -> None:
    async def run() -> None:
        settings = Settings(table_selector_embedding_enabled=False)
        selector = TableSelector(settings)
        old_gold = {
            "tableName": "gold.gold_llm_usage_daily",
            "database": "gold",
            "columns": [
                {"name": "metric_date"},
                {"name": "model"},
                {"name": "generation_count"},
            ],
        }
        selection = await selector.select(
            "show request cost trend by source app for 30 days",
            [old_gold, SERVER_MART_TABLE],
            datasource="clickhouse",
            min_tables=1,
            max_tables=2,
        )
        assert selection.primary_table is not None
        assert selection.primary_table["tableName"] == SERVER_MART_TABLE["tableName"]

    asyncio.run(run())


def test_table_selector_excludes_disabled_analytics_tables() -> None:
    async def run() -> None:
        settings = Settings(
            table_selector_embedding_enabled=False,
            disabled_analytics_tables=SERVER_MART_TABLE["tableName"],
        )
        selection = await TableSelector(settings).select(
            "show request cost trend by source app for 30 days",
            [GOLD_OBSERVABILITY_TABLE, SERVER_MART_TABLE],
            datasource="clickhouse",
            min_tables=1,
            max_tables=2,
        )

        assert selection.primary_table is not None
        assert selection.primary_table["tableName"] == GOLD_OBSERVABILITY_TABLE["tableName"]
        assert all(
            table["tableName"] != SERVER_MART_TABLE["tableName"]
            for table in selection.tables
        )

    asyncio.run(run())


def test_explicit_staging_request_excludes_curated_layers() -> None:
    async def run() -> None:
        settings = Settings(table_selector_embedding_enabled=False)
        selector = TableSelector(settings)
        staging = {
            "tableName": "vsf_stg.staging_llm_generations",
            "database": "vsf_stg",
            "columns": [
                {"name": "started_at"},
                {"name": "model"},
                {"name": "total_tokens"},
                {"name": "source_app_id"},
            ],
        }
        selection = await selector.select(
            "show raw staging event detail",
            [SERVER_MART_TABLE, staging],
            datasource="clickhouse",
            min_tables=1,
            max_tables=2,
        )
        assert [table["database"] for table in selection.tables] == ["vsf_stg"]

    asyncio.run(run())


def test_deterministic_sql_supports_server_mart_columns() -> None:
    sql = _generate_sql(
        "show request and cost trend for 30 days",
        SERVER_MART_TABLE,
        100,
        "clickhouse",
    )
    assert "date AS metric_date" in sql
    assert "sum(request_count) AS request_count" in sql
    assert "sum(total_cost_usd)" in sql
    assert "FROM vsf_mart.gold_app_daily_cost" in sql


def test_multi_agent_uses_deterministic_plan_without_llm_planner() -> None:
    async def run() -> None:
        settings = Settings(
            openrouter_api_key="",
            openai_api_key="",
            table_selector_embedding_enabled=False,
        )
        orchestrator = MultiAgentOrchestrator(
            settings,
            FakeChartAgent(),  # type: ignore[arg-type]
            llm=FakePlannerlessLlm(),  # type: ignore[arg-type]
        )
        result = await orchestrator.answer(
            "show LLM request cost trend for 30 days",
            include_synthesis=False,
        )
        assert result["status"] == "success"
        assert result["agentsUsed"] == ["chart_agent"]
        assert result["orchestration"]["planner"]["rationale"].startswith(
            "Deterministic server fallback"
        )

    asyncio.run(run())


def test_log_error_lookup_prefers_silver_log_detail_table() -> None:
    async def run() -> None:
        settings = Settings(table_selector_embedding_enabled=False)
        selector = TableSelector(settings)
        selection = await selector.select(
            "loi log cua hoang_ai_chat la ve gi vay",
            [GOLD_OBSERVABILITY_TABLE, SILVER_LOGS_TABLE],
            datasource="clickhouse",
            min_tables=1,
            max_tables=2,
        )
        assert selection.primary_table is not None
        assert selection.primary_table["tableName"] == "silver.silver_system_logs"
        assert selection.candidates[0]["sqlLinkOperation"] == "log_error_detail"

    asyncio.run(run())


def test_log_error_lookup_sql_filters_error_severity_and_source_app() -> None:
    sql = _generate_sql(
        "loi log cua hoang_ai_chat la ve gi vay",
        SILVER_LOGS_TABLE,
        100,
        "clickhouse",
    )

    assert "FROM silver.silver_system_logs" in sql
    assert "severity" in sql
    assert "message" in sql
    assert "lower(severity) IN ('error', 'fatal', 'critical')" in sql
    assert "positionCaseInsensitiveUTF8(source_app, 'hoang_ai_chat')" in sql
    assert "positionCaseInsensitiveUTF8(service_name, 'hoang-ai-chat')" in sql


def test_log_error_lookup_rejects_span_only_sql_candidate() -> None:
    assert not _plan_matches_intent(
        "loi log cua hoang_ai_chat la ve gi vay",
        "SELECT source_app, span_count, error_span_count "
        "FROM gold.gold_system_observability_daily",
        intent="lookup",
    )
    assert _plan_matches_intent(
        "loi log cua hoang_ai_chat la ve gi vay",
        "SELECT event_time, severity, message "
        "FROM silver.silver_system_logs "
        "WHERE lower(severity) IN ('error', 'fatal', 'critical')",
        intent="lookup",
    )


def test_multi_agent_routes_log_error_lookup_to_chart_agent() -> None:
    async def run() -> None:
        settings = Settings(table_selector_embedding_enabled=False)
        orchestrator = MultiAgentOrchestrator(
            settings,
            FakeChartAgent(),  # type: ignore[arg-type]
            llm=FakeInsightPlanner(),  # type: ignore[arg-type]
        )
        plan = build_request_plan("loi log cua hoang_ai_chat la ve gi vay")
        agent_plan = await orchestrator._agent_plan(
            "loi log cua hoang_ai_chat la ve gi vay",
            target="auto",
            limit=100,
            conversation=[],
            memory={},
            request_plan=plan,
        )

        assert agent_plan["intent"] == "chart"
        assert agent_plan["agents"] == ["chart_agent"]
        assert "log-detail SQL" in agent_plan["rationale"]

    asyncio.run(run())


def test_request_plan_keeps_overview_as_dashboard_despite_llm_slots() -> None:
    plan = build_request_plan(
        "Tong quan observe platform 24h qua the nao?",
        llm_slots={
            "intent_type": {"value": "lookup", "confidence": 0.99},
            "domain": {"value": "observability", "confidence": 0.99},
        },
    )

    assert plan["slots"]["intent_type"]["value"] == "dashboard"


def test_request_plan_preserves_multi_dimension_coverage() -> None:
    plan = build_request_plan(
        "cost tung model theo tung ngay cua tung source app, khong bo sot source nao",
        target="clickhouse",
    )

    assert plan["slots"]["dimensions"]["value"] == ["time", "model", "source_app"]
    assert plan["slots"]["coverage"]["value"] == ["source_app"]
    assert plan["slots"]["intent_type"]["value"] == "time_series"


def test_chart_spec_uses_requested_dimensions_as_composite_series() -> None:
    rows = [
        {"metric_date": "2026-06-01", "source_app": "tuan_ai_chat", "model": "gpt-4o", "total_cost_usd": 1.2},
        {"metric_date": "2026-06-01", "source_app": "bach_ai_chat", "model": "gpt-4o", "total_cost_usd": 0.8},
        {"metric_date": "2026-06-02", "source_app": "tuan_ai_chat", "model": "claude-3-5-sonnet", "total_cost_usd": 1.5},
    ]

    spec = infer_chart_spec(
        rows,
        intent="time_series",
        preferred_y=["total_cost_usd"],
        requested_dimensions=["time", "model", "source_app"],
    )

    assert spec["type"] == "line"
    assert spec["x"] == "metric_date"
    assert spec["y"] == ["total_cost_usd"]
    assert spec["series"] == ["model", "source_app"]


def test_chart_spec_infers_series_from_repeated_time_grain() -> None:
    rows = [
        {"metric_date": "2026-06-01", "source_app": "app_a", "model": "gpt-4o", "total_cost_usd": 1.2},
        {"metric_date": "2026-06-01", "source_app": "app_b", "model": "gpt-4o-mini", "total_cost_usd": 0.8},
        {"metric_date": "2026-06-02", "source_app": "app_a", "model": "gpt-4o", "total_cost_usd": 1.5},
    ]

    spec = infer_chart_spec(
        rows,
        intent="time_series",
        preferred_y=["total_cost_usd"],
    )

    assert spec["type"] == "line"
    assert spec["x"] == "metric_date"
    assert spec["y"] == ["total_cost_usd"]
    assert spec["series"] == ["source_app", "model"]

def test_dimension_aware_sql_fallback_groups_by_all_requested_grain() -> None:
    plan = build_request_plan(
        "cost tung model theo tung ngay cua tung source app, khong bo sot source nao",
        target="clickhouse",
    )

    sql = _generate_sql(
        "cost tung model theo tung ngay cua tung source app, khong bo sot source nao",
        SERVER_MART_TABLE,
        100,
        "clickhouse",
        request_plan=plan,
    )

    assert "date AS metric_date" in sql
    assert "model" in sql
    assert "source_app_id AS source_app" in sql
    assert "GROUP BY date, model, source_app_id" in sql
    assert "round(sum(total_cost_usd), 6) AS total_cost_usd" in sql
