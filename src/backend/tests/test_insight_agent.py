"""Tests for Specialist Agents and specialist_utils helper functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.agents.specialists import (
    AgentResponse,
    AgentTask,
    ChartAgent,
    Evidence,
    ForecastAgent,
    InsightAgent,
    QaAgent,
    SpecialistTableResolution,
    SummarizerAgent,
    _agent_synthesis_timeout,
    _artifact_row_count,
    _asks_log_error_lookup,
    _clickhouse_where_clause,
    _detect_anomalies,
    _elapsed_ms,
    _extract_entity_filters,
    _filter_warnings,
    _force_chart_for_log_error_lookup,
    _force_request_plan_route,
    _legacy_intent,
    _llm_usage_from_result,
    _median,
    _merge_agent_usage,
    _merge_attempts,
    _merge_metadata_context,
    _merge_searched_tables,
    _merge_selected_tables,
    _merge_sql_generation,
    _merge_table_selection,
    _metadata_evidence,
    _normalize_insight_row,
    _normalize_qa_row,
    _parse_days,
    _primary_artifact_result,
    _resolve_specialist_table,
    _result_evidence,
    _run_llm_synthesis,
    _slot_value,
    _sql_evidence,
)
from app.security import Principal


@pytest.mark.asyncio
async def test_chart_agent_run() -> None:
    monitoring = MagicMock()
    monitoring.answer = AsyncMock(return_value={
        "status": "success",
        "answer": "Here is the chart",
        "sql": "SELECT 1",
        "rows": [{"col": 1}],
        "llmUsage": {"total_tokens": 120},
    })

    agent = ChartAgent(monitoring_agent=monitoring)
    task = AgentTask(task_id="t-1", agent="chart_agent", objective="Show chart")
    res = await agent.run(
        task,
        question="Show chart",
        target="auto",
        limit=50,
        conversation=[],
        memory={},
        principal=Principal(user_id="u1", username="user1", role="viewer", team="BI"),
        include_synthesis=True,
        event_sink=lambda _: None,
    )

    assert isinstance(res, AgentResponse)
    assert res.status == "success"
    assert res.text == "Here is the chart"
    assert res.usage == {"total_tokens": 120}
    assert len(res.artifacts) == 1
    assert res.artifacts[0]["type"] == "query_result"


@pytest.mark.asyncio
async def test_insight_agent_run() -> None:
    clickhouse = MagicMock()
    clickhouse.query = AsyncMock(side_effect=[
        # dbt_runs query
        [{"run_id": "r-1", "status": "success", "started_at": "2026-06-26", "finished_at": "2026-06-26"}],
        # metadata table lookup
        [{"tableName": "vsf_raw.otel_traces", "database": "vsf_raw", "columns": [{"name": "metric_date"}, {"name": "service_name"}, {"name": "source_app"}, {"name": "span_count"}, {"name": "error_span_count"}, {"name": "avg_span_duration_ms"}, {"name": "p95_span_duration_ms"}]}],
        # main query
        [
            {"service_name": "auth_service", "source_app": "mobile", "span_count": 1000, "error_span_count": 50, "error_rate_pct": 5.0, "avg_latency_ms": 120.5, "p95_latency_ms": 350.0},
            {"service_name": "catalog_service", "source_app": "web", "span_count": 5000, "error_span_count": 5, "error_rate_pct": 0.1, "avg_latency_ms": 45.0, "p95_latency_ms": 95.0},
        ],
    ])

    postgres = MagicMock()
    postgres.query = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.interpret_insight = AsyncMock(return_value={"status": "success", "answer": "Insight summary: auth_service has elevated errors.", "usage": {"total_tokens": 150}})

    settings = MagicMock()
    settings.agent_synthesis_timeout_seconds = 5.0

    metadata = MagicMock()
    metadata.get_table = AsyncMock(return_value={"tableName": "vsf_raw.otel_traces", "columns": [{"name": "metric_date"}, {"name": "service_name"}, {"name": "source_app"}, {"name": "span_count"}, {"name": "error_span_count"}, {"name": "avg_span_duration_ms"}, {"name": "p95_span_duration_ms"}]})
    metadata.search_tables = AsyncMock(return_value=[{"tableName": "vsf_raw.otel_traces"}])

    agent = InsightAgent(clickhouse=clickhouse, postgres=postgres, settings=settings, llm=llm, metadata=metadata)
    task = AgentTask(task_id="t-2", agent="insight_agent", objective="Check auth_service latency")
    res = await agent.run(
        task,
        question="Check auth_service latency",
        target="auto",
        limit=50,
        conversation=[],
        memory={},
        principal=Principal(user_id="u1", username="user1", role="viewer", team="BI"),
        include_synthesis=True,
        event_sink=lambda _: None,
    )

    assert res.status in {"success", "partial"}
    assert res.text == "Insight summary: auth_service has elevated errors." or "auth_service" in res.text
    assert len(res.artifacts) == 1
    assert res.artifacts[0]["type"] == "root_cause_analysis"


@pytest.mark.asyncio
async def test_qa_agent_run() -> None:
    clickhouse = MagicMock()
    clickhouse.query = AsyncMock(side_effect=[
        # metadata table lookup
        [{"tableName": "vsf_raw.ingestion_batches", "database": "vsf_raw", "columns": [{"name": "metric_date"}, {"name": "source_app"}, {"name": "batch_count"}, {"name": "failed_batch_count"}, {"name": "quarantined_batch_count"}, {"name": "loaded_row_count"}]}],
        # main query
        [
            {"source_app": "erp_sync", "batch_count": 100, "failed_batch_count": 5, "quarantined_batch_count": 2, "loaded_row_count": 50000, "failed_batch_rate_pct": 5.0},
        ],
    ])

    postgres = MagicMock()
    postgres.query = AsyncMock(return_value=[{"status": "error", "query_count": 12}])

    llm = MagicMock()
    llm.interpret_qa = AsyncMock(return_value={"status": "success", "answer": "QA Health: erp_sync had 5 failed batches.", "usage": {"total_tokens": 90}})

    settings = MagicMock()
    settings.agent_synthesis_timeout_seconds = 5.0

    metadata = MagicMock()
    metadata.get_table = AsyncMock(return_value={"tableName": "vsf_raw.ingestion_batches", "columns": [{"name": "metric_date"}, {"name": "source_app"}, {"name": "batch_count"}, {"name": "failed_batch_count"}, {"name": "quarantined_batch_count"}, {"name": "loaded_row_count"}]})
    metadata.search_tables = AsyncMock(return_value=[{"tableName": "vsf_raw.ingestion_batches"}])

    agent = QaAgent(clickhouse=clickhouse, postgres=postgres, settings=settings, llm=llm, metadata=metadata)
    task = AgentTask(task_id="t-3", agent="qa_agent", objective="Check ingestion health")
    res = await agent.run(
        task,
        question="Check ingestion health for erp_sync",
        target="auto",
        limit=50,
        conversation=[],
        memory={},
        principal=Principal(user_id="u1", username="user1", role="viewer", team="BI"),
        include_synthesis=True,
        event_sink=lambda _: None,
    )

    assert res.status in {"success", "partial"}
    assert res.text == "QA Health: erp_sync had 5 failed batches." or "erp_sync" in res.text
    assert len(res.artifacts) == 1
    assert res.artifacts[0]["type"] == "qa_health"


@pytest.mark.asyncio
async def test_forecast_agent_run() -> None:
    clickhouse = MagicMock()
    clickhouse.query = AsyncMock(side_effect=[
        # metadata table lookup
        [{"tableName": "vsf_raw.daily_sales", "database": "vsf_raw", "columns": [{"name": "metric_date", "type": "Date"}, {"name": "total_sales", "type": "Float64"}, {"name": "service_name", "type": "String"}]}],
        # main query
        [
            {"metric_date": "2026-06-01", "forecast_value": 1000.0},
            {"metric_date": "2026-06-02", "forecast_value": 1100.0},
            {"metric_date": "2026-06-03", "forecast_value": 1210.0},
        ],
    ])

    llm = MagicMock()
    llm.interpret_forecast = AsyncMock(return_value={"status": "success", "answer": "Sales will increase by 10%.", "usage": {"total_tokens": 110}})

    settings = MagicMock()
    settings.agent_synthesis_timeout_seconds = 5.0

    metadata = MagicMock()
    metadata.get_table = AsyncMock(return_value={"tableName": "vsf_raw.daily_sales", "columns": [{"name": "metric_date"}, {"name": "total_sales"}, {"name": "service_name"}]})
    metadata.search_tables = AsyncMock(return_value=[{"tableName": "vsf_raw.daily_sales"}])

    agent = ForecastAgent(clickhouse=clickhouse, settings=settings, llm=llm, metadata=metadata)
    task = AgentTask(task_id="t-4", agent="forecast_agent", objective="Predict sales")
    res = await agent.run(
        task,
        question="Predict total_sales for next week",
        target="auto",
        limit=50,
        conversation=[],
        memory={},
        principal=Principal(user_id="u1", username="user1", role="viewer", team="BI"),
        include_synthesis=True,
        event_sink=lambda _: None,
    )

    assert res.status in {"success", "partial"}
    assert res.text == "Sales will increase by 10%." or "sales" in res.text.lower() or "forecast" in res.text.lower()
    assert len(res.artifacts) == 1
    assert res.artifacts[0]["type"] == "forecast"


@pytest.mark.asyncio
async def test_summarizer_agent_summarize() -> None:
    llm = MagicMock()
    llm.summarize_agent_responses = AsyncMock(return_value={"status": "success", "answer": "Consolidated summary of all agents.", "usage": {"total_tokens": 80}})

    summarizer = SummarizerAgent(llm=llm)
    responses = [
        AgentResponse(task_id="1", agent="insight_agent", status="success", text="Insight done", confidence=0.9, warnings=["High latency"]),
        AgentResponse(task_id="2", agent="qa_agent", status="success", text="QA done", confidence=0.85, warnings=[]),
    ]

    res = await summarizer.summarize("What is the status?", responses)
    assert res == "Consolidated summary of all agents."
    assert summarizer.last_usage["total_tokens"] == 80


def test_specialist_utils_helpers() -> None:
    assert _parse_days("last 14 days", default=7) == 14
    assert _parse_days("invalid", default=7) == 7

    assert _median([1.0, 2.0, 3.0]) == 2.0
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert _median([]) == 0.0

    assert _normalize_insight_row({"service_name": "s1", "span_count": "100", "p95_latency_ms": 12.5}) == {
        "service_name": "s1", "source_app": "", "span_count": 100, "error_span_count": 0,
        "error_rate_pct": 0.0, "avg_latency_ms": 0.0, "p95_latency_ms": 12.5
    }

    assert _normalize_qa_row({"source_app": "app1", "batch_count": "50"}) == {
        "source_app": "app1", "batch_count": 50, "failed_batch_count": 0,
        "quarantined_batch_count": 0, "loaded_row_count": 0, "failed_batch_rate_pct": 0.0
    }

    assert _asks_log_error_lookup("find log errors for service X") is True
    assert _asks_log_error_lookup("show daily active users") is False

    assert _artifact_row_count({"rows": [1, 2, 3], "rowCount": 3}) == 3

    assert _extract_entity_filters("Check auth_service on mobile app for model gpt-4") == {
        "service_name": "auth_service", "source_app": "mobile", "model": "gpt-4"
    }

    where = _clickhouse_where_clause(days=7, filters={"service_name": "auth_service"}, allowed_columns={"service_name"})
    assert "service_name = 'auth_service'" in where
    assert "INTERVAL 7 DAY" in where

    ev = _sql_evidence("SELECT 1", 10, "vsf_raw.users")
    assert ev.source == "sql"
    assert ev.reference == "SELECT 1"
    assert "10 rows" in ev.summary

    res = SpecialistTableResolution(table={"tableName": "vsf_raw.t1"}, metadata_source="datahub", table_selection={}, candidate_count=1, required_columns=["col1"])
    meta_ev = _metadata_evidence(res, "target")
    assert meta_ev.source == "metadata"
    assert meta_ev.reference == "vsf_raw.t1"

    warnings = _filter_warnings({"service_name": "auth"}, {"service_name"}, {"metric_date"})
    assert any("service_name" in w for w in warnings)
