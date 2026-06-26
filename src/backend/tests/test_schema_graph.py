"""Tests for SchemaGraph and TableNode relationships."""

from __future__ import annotations

import pytest
from app.agents.model2.schema_graph import (
    JoinPath,
    SchemaEdge,
    SchemaGraph,
    TableNode,
    _looks_like_fk_column,
    _node_from_table,
    _table_match_text,
    _unique_existing,
    _useful_shared_column,
)


def test_table_node_and_edge_dict() -> None:
    node = TableNode(
        name="vsf_raw.orders",
        columns={"id", "user_id", "amount", "created_at"},
        column_types={"id": "UUID", "user_id": "UUID", "amount": "Decimal"},
        pk_columns={"id"},
        fk_columns={"user_id": "user"},
        schema="vsf_raw",
        description="Customer orders table",
        raw_table={"tableName": "vsf_raw.orders"},
    )
    data = node.to_dict()
    assert data["name"] == "vsf_raw.orders"
    assert data["description"] == "Customer orders table"
    assert data["columns"] == ["amount", "created_at", "id", "user_id"]
    assert data["pkColumns"] == ["id"]
    assert data["fkColumns"] == {"user_id": "user"}

    edge = SchemaEdge(
        source="vsf_raw.orders",
        target="vsf_raw.users",
        edge_type="FK",
        columns=["user_id"],
        weight=1.0,
    )
    edge_dict = edge.to_dict()
    assert edge_dict["from"] == "vsf_raw.orders"
    assert edge_dict["to"] == "vsf_raw.users"
    assert edge_dict["type"] == "FK"
    assert edge_dict["columns"] == ["user_id"]
    assert edge_dict["weight"] == 1.0


def test_join_path_dict() -> None:
    edge = SchemaEdge(
        source="vsf_raw.orders",
        target="vsf_raw.users",
        edge_type="FK",
        columns=["user_id"],
        weight=1.0,
    )
    path = JoinPath(
        tables=["vsf_raw.orders", "vsf_raw.users"],
        edges=[edge],
        total_weight=1.0,
        bridge_tables=[],
    )
    path_dict = path.to_dict()
    assert path_dict["tables"] == ["vsf_raw.orders", "vsf_raw.users"]
    assert path_dict["totalWeight"] == 1.0
    assert path_dict["bridgeTables"] == []
    assert len(path_dict["edges"]) == 1


def test_schema_graph_build_from_metadata() -> None:
    tables = [
        {
            "tableName": "vsf_raw.users",
            "database": "vsf_raw",
            "description": "User profile table",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "email", "type": "String"},
                {"name": "created_at", "type": "DateTime"},
            ],
        },
        {
            "tableName": "vsf_raw.orders",
            "database": "vsf_raw",
            "description": "Store orders",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "user_id", "type": "UUID"},
                {"name": "total_amount", "type": "Float64"},
            ],
        },
        {
            "tableName": "vsf_raw.order_items",
            "database": "vsf_raw",
            "description": "Items per order",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "order_id", "type": "UUID"},
                {"name": "product_id", "type": "UUID"},
            ],
        },
        {
            "tableName": "vsf_raw.products",
            "database": "vsf_raw",
            "description": "Product catalog",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "name", "type": "String"},
                {"name": "price", "type": "Float64"},
            ],
        },
        {
            "tableName": "vsf_raw.empty_table",
        },
    ]
    lineage = [
        {"upstream": "urn:li:dataset:(urn:li:dataPlatform:clickhouse,vsf_raw.users,PROD)", "downstream": "vsf_raw.orders"}
    ]

    graph = SchemaGraph()
    graph.build_from_metadata(tables, lineage)

    assert len(graph.nodes) == 5
    assert len(graph.edges) > 0
    assert graph.table("vsf_raw.users") == tables[0]
    assert graph.degree("vsf_raw.users") > 0

    edge_list = graph.to_edge_list()
    assert any(e["from"] == "vsf_raw.orders" and e["to"] == "vsf_raw.users" for e in edge_list)

    paths = graph.find_join_paths(["vsf_raw.users", "vsf_raw.products"], max_hops=4)
    assert len(paths) > 0

    expanded = graph.expand_with_bridges(["vsf_raw.users", "vsf_raw.products"], "find total price for user email", max_hops=4)
    assert "vsf_raw.orders" in expanded or "vsf_raw.order_items" in expanded

    sub = graph.subgraph(["vsf_raw.users", "vsf_raw.orders"])
    assert len(sub.nodes) == 2
    assert len(sub.edges) >= 2


def test_schema_graph_helper_functions() -> None:
    assert _looks_like_fk_column("user_id") is True
    assert _looks_like_fk_column("source_app_id") is False
    assert _looks_like_fk_column("id") is False

    assert _useful_shared_column("metric_date") is True
    assert _useful_shared_column("user_id") is True
    assert _useful_shared_column("status") is False

    assert _table_match_text("dim_users") == "users"
    assert _table_match_text("fct_orders") == "orders"
    assert _table_match_text("vsf_raw.products") == "products"

    nodes = {"a": TableNode("a", set(), {}, set(), {}, "", ""), "b": TableNode("b", set(), {}, set(), {}, "", "")}
    assert _unique_existing(["a", "b", "c", "a"], nodes) == ["a", "b"]
