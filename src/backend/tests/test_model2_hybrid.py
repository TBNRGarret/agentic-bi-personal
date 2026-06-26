from __future__ import annotations

from app.agents.model2.contracts import SchemaRetrievalResult
from app.agents.model2.hybrid_selector import HybridSelector
from app.agents.model2.schema_retriever import route_datasource, tables_for_datasource
from app.agents.model2.workflow_graph import build_model2_workflow_graph


SERVER_MART_TABLE = {
    "tableName": "vsf_mart.gold_app_daily_cost",
    "database": "vsf_mart",
    "datasource": "clickhouse",
    "description": "Daily LLM request volume, tokens, cost, and latency by app and model.",
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

UNRELATED_TABLE = {
    "tableName": "gold.product_catalog_snapshot",
    "database": "gold",
    "datasource": "clickhouse",
    "description": "Product catalog snapshot.",
    "columns": [
        {"name": "sku", "type": "String"},
        {"name": "title", "type": "String"},
        {"name": "category", "type": "String"},
    ],
}


def test_model2_selector_prunes_schema_but_keeps_metric_columns() -> None:
    retrieval = SchemaRetrievalResult(
        datasource="clickhouse",
        metadata_source="datahub",
        database_description="Agentic BI ClickHouse analytics warehouse",
        tables=[SERVER_MART_TABLE, UNRELATED_TABLE],
        ranked_tables=[SERVER_MART_TABLE, UNRELATED_TABLE],
        graph_edges=[
            {
                "from": "vsf_stg.staging_llm_generations",
                "to": SERVER_MART_TABLE["tableName"],
                "type": "LINEAGE",
            }
        ],
    )

    result = HybridSelector().select(
        "show cost and token trend by model for each source app",
        retrieval,
        request_plan={
            "slots": {
                "metric": {"value": ["cost", "token"]},
                "dimensions": {"value": ["time", "model", "source_app"]},
            }
        },
    )

    assert result.selected_table_names[0] == SERVER_MART_TABLE["tableName"]
    assert "# Table: vsf_mart.gold_app_daily_cost" in result.schema_prompt
    assert "total_cost_usd" in result.schema_prompt
    assert "total_tokens" in result.schema_prompt
    assert result.graph_edges[0]["type"] == "LINEAGE"


def test_model2_datasource_routing_and_catalog_filtering() -> None:
    assert route_datasource("show recent failed query audit errors", "auto") == "dwh"
    assert route_datasource("show llm cost trend", "auto") == "clickhouse"

    tables = [
        {"tableName": "ops.agent_query_audit", "database": "ops"},
        {"tableName": SERVER_MART_TABLE["tableName"], "database": "vsf_mart"},
        {"tableName": "raw.raw_orders", "database": "raw"},
    ]
    assert [table["tableName"] for table in tables_for_datasource(tables, "dwh")] == [
        "ops.agent_query_audit"
    ]
    assert [table["tableName"] for table in tables_for_datasource(tables, "clickhouse")] == [
        SERVER_MART_TABLE["tableName"]
    ]


def test_model2_workflow_graph_marks_failed_stage() -> None:
    graph = build_model2_workflow_graph(
        question="show llm costs",
        target="auto",
        status="error",
        datasource="clickhouse",
        selected_tables=[SERVER_MART_TABLE["tableName"]],
        execution_time_ms=42,
        failed_stage="executor",
    )

    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["hybrid_selector"]["sublabel"] == SERVER_MART_TABLE["tableName"]
    assert nodes["executor"]["status"] == "error"
    assert nodes["response"]["status"] == "error"

