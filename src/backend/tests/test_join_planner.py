from __future__ import annotations

from app.agents.model2.contracts import SchemaRetrievalResult, SelectorResult
from app.agents.model2.entity_detector import BusinessEntityDetector
from app.agents.model2.join_planner import JoinPlanner


SALES_TABLE = {
    "tableName": "vsf_mart.fct_sales",
    "database": "vsf_mart",
    "datasource": "clickhouse",
    "description": "Sales revenue fact table by publisher.",
    "columns": [
        {"name": "date", "type": "Date"},
        {"name": "publisher_id", "type": "String"},
        {"name": "total_revenue", "type": "Float64"},
    ],
}

PUBLISHER_TABLE = {
    "tableName": "vsf_mart.dim_publishers",
    "database": "vsf_mart",
    "datasource": "clickhouse",
    "description": "Publisher dimension table.",
    "columns": [
        {"name": "publisher_id", "type": "String"},
        {"name": "publisher_name", "type": "String"},
    ],
}


def _retrieval() -> SchemaRetrievalResult:
    return SchemaRetrievalResult(
        datasource="clickhouse",
        metadata_source="unit",
        database_description="mart",
        tables=[SALES_TABLE, PUBLISHER_TABLE],
        ranked_tables=[SALES_TABLE, PUBLISHER_TABLE],
        graph_edges=[
            {
                "from": "vsf_mart.fct_sales",
                "to": "vsf_mart.dim_publishers",
                "type": "SHARED_COLUMN",
                "columns": ["publisher_id"],
                "weight": 0.8,
            }
        ],
        join_paths=[
            {
                "tables": ["vsf_mart.fct_sales", "vsf_mart.dim_publishers"],
                "edges": [
                    {
                        "from": "vsf_mart.fct_sales",
                        "to": "vsf_mart.dim_publishers",
                        "type": "SHARED_COLUMN",
                        "columns": ["publisher_id"],
                        "weight": 0.8,
                    }
                ],
                "bridgeTables": [],
                "totalWeight": 0.8,
            }
        ],
    )


def _selector() -> SelectorResult:
    return SelectorResult(
        selected_tables=[SALES_TABLE, PUBLISHER_TABLE],
        selected_table_names=["vsf_mart.fct_sales", "vsf_mart.dim_publishers"],
        schema_prompt="# Table: vsf_mart.fct_sales\n# Table: vsf_mart.dim_publishers",
        decisions=[],
        confidence=0.9,
        strategy="unit",
    )


def test_business_entity_detector_maps_glossary_to_schema_hints() -> None:
    result = BusinessEntityDetector().detect(
        "doanh thu theo publisher tháng này",
        tables=[SALES_TABLE, PUBLISHER_TABLE],
        request_plan={},
    )

    canonical = {entity.canonical for entity in result.entities}
    assert {"revenue", "publisher", "current_month"} <= canonical
    assert "publisher_id" in result.column_mentions or any(
        "publisher_id" in entity.mapped_columns for entity in result.entities
    )


def test_join_planner_builds_explicit_join_hint_block() -> None:
    join_plan = JoinPlanner().plan(
        "doanh thu theo publisher tháng này",
        retrieval=_retrieval(),
        selector=_selector(),
        request_plan={},
    )

    assert join_plan.status == "ready"
    assert join_plan.selected_paths
    selected = join_plan.to_dict()["selectedPaths"][0]
    assert selected["tables"] == ["vsf_mart.fct_sales", "vsf_mart.dim_publishers"]
    assert selected["joinSteps"][0]["columns"] == ["publisher_id"]
    assert "MODEL2 JOIN HINTS" in join_plan.hint_block
    assert "publisher_id" in join_plan.hint_block
    assert join_plan.validation["requiredJoinConditions"]
    assert join_plan.decomposition_hints[0]["strategy"] == "single_query_join"
