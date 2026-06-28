"""MCP server exposing DataHub metadata tools for Agentic BI.

Run with:
    python -m metadata.mcp_server

The default transport is stdio, which is the safest production integration for
desktop/IDE agents. Set DATAHUB_MCP_TRANSPORT=sse when a remote MCP client must
connect over HTTP.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without deps.
    raise SystemExit(
        "Missing MCP runtime. Install dependencies with `pip install -r requirements.txt`."
    ) from exc

from metadata.mcp_service import DEFAULT_PLATFORM, DEFAULT_SCHEMA, build_service


mcp = FastMCP("agentic-bi-datahub")
service = build_service()


@mcp.tool()
def datahub_health(schema: str = DEFAULT_SCHEMA, platform: str = DEFAULT_PLATFORM) -> dict[str, Any]:
    """Check DataHub connectivity and return a small metadata availability summary."""
    return service.health(schema=schema, platform=platform)


@mcp.tool()
def search_tables(
    question: str,
    schema: str = DEFAULT_SCHEMA,
    platform: str = DEFAULT_PLATFORM,
    limit: int | None = None,
) -> dict[str, Any]:
    """Find relevant executable datasets for a natural-language analytics question."""
    return service.search_tables(question=question, schema=schema, platform=platform, limit=limit)


@mcp.tool()
def get_table_schema(dataset_urn: str, include_freshness: bool = True) -> dict[str, Any]:
    """Fetch bounded column metadata for one DataHub dataset URN."""
    return service.get_table_schema(dataset_urn=dataset_urn, include_freshness=include_freshness)


@mcp.tool()
def list_schema_fields(dataset_urn: str) -> dict[str, Any]:
    """Fetch bounded field metadata for one DataHub dataset URN."""
    return service.list_schema_fields(dataset_urn=dataset_urn)


@mcp.tool()
def get_lineage(dataset_urn: str, depth: int = 3) -> dict[str, Any]:
    """Fetch upstream lineage for one DataHub dataset URN."""
    return service.get_lineage(dataset_urn=dataset_urn, depth=depth)


@mcp.tool()
def get_freshness(dataset_urn: str) -> dict[str, Any]:
    """Fetch freshness metadata for one DataHub dataset URN."""
    return service.get_freshness(dataset_urn=dataset_urn)


@mcp.tool()
def get_table_context_for_question(
    question: str,
    schema: str = DEFAULT_SCHEMA,
    platform: str = DEFAULT_PLATFORM,
    limit: int | None = None,
    include_lineage: bool = False,
) -> dict[str, Any]:
    """Fetch compact table, column, freshness, and optional lineage context for SQL planning."""
    return service.get_table_context_for_question(
        question=question,
        schema=schema,
        platform=platform,
        limit=limit,
        include_lineage=include_lineage,
    )


def main() -> None:
    transport = os.getenv("DATAHUB_MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
