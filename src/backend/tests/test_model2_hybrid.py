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
    "tableName": "vsf_mart.product_catalog_snapshot",
    "database": "vsf_mart",
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


def test_model2_selector_drops_small_unrelated_tables() -> None:
    retrieval = SchemaRetrievalResult(
        datasource="clickhouse",
        metadata_source="datahub",
        database_description="Agentic BI ClickHouse analytics warehouse",
        tables=[SERVER_MART_TABLE, UNRELATED_TABLE],
        ranked_tables=[SERVER_MART_TABLE, UNRELATED_TABLE],
    )

    result = HybridSelector().select(
        "show cost trend by model",
        retrieval,
        request_plan={
            "slots": {
                "metric": {"value": ["cost"]},
                "dimensions": {"value": ["time", "model"]},
            }
        },
    )

    assert result.selected_table_names == [SERVER_MART_TABLE["tableName"]]
    product_decision = next(
        decision
        for decision in result.decisions
        if decision["tableName"] == UNRELATED_TABLE["tableName"]
    )
    assert product_decision["decision"] == "drop_all"
    assert "vsf_mart.product_catalog_snapshot" not in result.schema_prompt


def test_model2_selector_prunes_wide_table_to_requested_roles() -> None:
    wide_table = {
        **SERVER_MART_TABLE,
        "columns": SERVER_MART_TABLE["columns"]
        + [
            {"name": "request_payload", "type": "String"},
            {"name": "raw_json", "type": "String"},
            {"name": "debug_notes", "type": "String"},
            {"name": "ingestion_metadata_json", "type": "String"},
            {"name": "unused_flag", "type": "UInt8"},
            {"name": "operator_comment", "type": "String"},
            {"name": "internal_partition", "type": "String"},
        ],
    }
    retrieval = SchemaRetrievalResult(
        datasource="clickhouse",
        metadata_source="datahub",
        database_description="Agentic BI ClickHouse analytics warehouse",
        tables=[wide_table],
        ranked_tables=[wide_table],
    )

    result = HybridSelector().select(
        "show cost trend by model",
        retrieval,
        request_plan={
            "slots": {
                "metric": {"value": ["cost"]},
                "dimensions": {"value": ["time", "model"]},
            }
        },
    )

    decision = result.decisions[0]
    assert decision["decision"] == "select_columns"
    assert {"date", "model", "total_cost_usd"} <= set(decision["selectedColumns"])
    assert "request_payload" not in decision["selectedColumns"]
    assert "raw_json" not in decision["selectedColumns"]
    assert "debug_notes" not in result.schema_prompt
    assert "roles: metric:cost" in result.schema_prompt


def test_model2_selector_preserves_graph_join_keys() -> None:
    orders = {
        "tableName": "mart.fct_orders",
        "database": "mart",
        "datasource": "clickhouse",
        "description": "Order revenue fact table",
        "columns": [
            {"name": "metric_date", "type": "Date"},
            {"name": "order_id", "type": "String"},
            {"name": "customer_id", "type": "String"},
            {"name": "total_amount", "type": "Float64"},
            {"name": "raw_payload", "type": "String"},
        ],
    }
    customers = {
        "tableName": "mart.dim_customers",
        "database": "mart",
        "datasource": "clickhouse",
        "description": "Customer dimension with region",
        "columns": [
            {"name": "customer_id", "type": "String"},
            {"name": "region", "type": "String"},
            {"name": "email", "type": "String"},
            {"name": "debug_blob", "type": "String"},
        ],
    }
    retrieval = SchemaRetrievalResult(
        datasource="clickhouse",
        metadata_source="datahub",
        database_description="Sales mart",
        tables=[orders, customers],
        ranked_tables=[orders, customers],
        join_paths=[
            {
                "tables": ["mart.fct_orders", "mart.dim_customers"],
                "edges": [
                    {
                        "from": "mart.fct_orders",
                        "to": "mart.dim_customers",
                        "type": "FK",
                        "columns": ["customer_id"],
                    }
                ],
            }
        ],
    )

    result = HybridSelector().select(
        "show total amount by customer region over time",
        retrieval,
        request_plan={
            "slots": {
                "metric": {"value": ["revenue"]},
                "dimensions": {"value": ["time", "region"]},
            }
        },
    )

    by_table = {decision["tableName"]: decision for decision in result.decisions}
    assert "customer_id" in by_table["mart.fct_orders"]["selectedColumns"]
    assert "customer_id" in by_table["mart.dim_customers"]["selectedColumns"]
    assert "region" in by_table["mart.dim_customers"]["selectedColumns"]
    assert "raw_payload" not in by_table["mart.fct_orders"]["selectedColumns"]
    assert "debug_blob" not in by_table["mart.dim_customers"]["selectedColumns"]


def test_model2_datasource_routing_and_catalog_filtering() -> None:
    assert route_datasource("show recent failed query audit errors", "auto") == "dwh"
    assert route_datasource("Show the most expensive agent queries", "auto") == "dwh"
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




def test_model2_memory_graph_uses_lineage_edges_without_falkordb() -> None:
    from app.agents.model2.graph_retriever import GraphSchemaRetriever
    from app.agents.model2.schema_retriever import SchemaRetriever

    stg_table = {
        "tableName": "vsf_stg.staging_llm_generations",
        "database": "vsf_stg",
        "datasource": "clickhouse",
        "description": "Staging LLM generations with model and token details.",
        "columns": [
            {"name": "generation_id", "type": "String"},
            {"name": "model", "type": "String"},
            {"name": "total_tokens", "type": "UInt64"},
        ],
    }
    mart_table = SERVER_MART_TABLE
    retriever = object.__new__(SchemaRetriever)
    retriever.graph_retriever = GraphSchemaRetriever()

    result = retriever._retrieve_from_memory_graph(
        "show token lineage by model",
        datasource="clickhouse",
        metadata_source="datahub",
        candidates=[stg_table, mart_table],
        selected_tables=[mart_table, stg_table],
        ranked_tables=[mart_table, stg_table],
        lineage_edges=[
            {
                "upstream": "vsf_stg.staging_llm_generations",
                "downstream": "vsf_mart.gold_app_daily_cost",
            }
        ],
        request_plan={"slots": {"metric": {"value": ["tokens"]}}},
        max_tables=5,
    )

    assert result is not None
    assert result.metadata_source == "datahub+memory_graph"
    assert result.retrieval["strategy"] == "memory_queryweaver_schema_graph"
    assert result.retrieval["lineageEdgeCount"] == 1
    assert any(edge["type"] == "LINEAGE" for edge in result.graph_edges)
    assert result.join_paths


def test_model2_refiner_candidate_helper_prefers_ordered_candidates() -> None:
    from app.agents.model2.orchestrator import _refiner_sql_candidates

    candidates = _refiner_sql_candidates(
        {
            "sql": "SELECT 1 LIMIT 10",
            "repairStrategy": "deterministic_first",
            "candidates": [
                {"sql": "SELECT 2 LIMIT 10", "strategy": "first"},
                {"sql": "SELECT 3 LIMIT 10", "strategy": "second"},
            ],
        }
    )

    assert [candidate["sql"] for candidate in candidates] == [
        "SELECT 1 LIMIT 10",
        "SELECT 2 LIMIT 10",
        "SELECT 3 LIMIT 10",
    ]
    assert candidates[0]["strategy"] == "deterministic_first"

    promoted = _refiner_sql_candidates(
        {
            "sql": "SELECT repaired LIMIT 10",
            "repairStrategy": "deterministic_first",
            "candidates": [
                {"sql": "SELECT broken LIMIT 10", "strategy": "clean_sql"},
                {"sql": "SELECT repaired LIMIT 10", "strategy": "schema_and_limit_guard"},
            ],
        }
    )
    assert promoted[0]["sql"] == "SELECT repaired LIMIT 10"
    assert promoted[0]["strategy"] == "schema_and_limit_guard"


def test_model2_datahub_catalog_selector_searches_then_inspects_schema() -> None:
    import asyncio

    from app.agents.model2.graph_retriever import GraphSchemaRetriever
    from app.agents.model2.schema_retriever import SchemaRetriever

    class FakeCatalogMetadata:
        async def relevant_tables(self, question: str, limit: int = 4):
            assert "cost" in question
            return [
                {
                    "urn": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,agentic_clickhouse.vsf_mart.gold_app_daily_cost,PROD)",
                    "tableName": SERVER_MART_TABLE["tableName"],
                    "database": "vsf_mart",
                    "datasource": "clickhouse",
                    "description": SERVER_MART_TABLE["description"],
                    "retrievalConfidence": 0.91,
                }
            ]

        async def list_schema_fields(self, table_name: str):
            assert "gold_app_daily_cost" in table_name
            return SERVER_MART_TABLE["columns"]

    class FakeSettings:
        datahub_gms_url = ""
        datahub_token = ""
        datahub_timeout_seconds = 1.0

    async def run():
        retriever = object.__new__(SchemaRetriever)
        retriever.metadata = FakeCatalogMetadata()
        retriever.settings = FakeSettings()
        retriever.graph_retriever = GraphSchemaRetriever()
        warnings: list[str] = []
        return await retriever._retrieve_from_datahub_catalog(
            "show cost trend by model",
            datasource="clickhouse",
            metadata_source="datahub",
            candidates=[],
            request_plan={"slots": {"metric": {"value": ["cost"]}}},
            min_tables=1,
            max_tables=3,
            warnings=warnings,
        )

    result = asyncio.run(run())

    assert result is not None
    assert result.retrieval["strategy"] == "datahub_mcp_catalog_selector"
    assert result.retrieval["workflow"] == "data_discovery_query_construction"
    assert result.retrieval["schemaInspectionTool"] == "datahub.list_schema_fields"
    assert result.table_selection["primaryTable"] == SERVER_MART_TABLE["tableName"]
    assert result.tables[0]["columns"][0]["name"] == "date"

