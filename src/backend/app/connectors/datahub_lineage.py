from __future__ import annotations

from typing import Any

import httpx


async def fetch_lineage(
    datahub_url: str,
    token: str,
    tables: list[dict[str, Any]],
    *,
    timeout_seconds: float = 5.0,
    max_tables: int = 20,
) -> list[dict[str, str]]:
    """Fetch dataset lineage edges from DataHub GraphQL.

    Returns normalized table-name edges:
    {"upstream": "schema.table_a", "downstream": "schema.table_b"}.
    """
    urn_to_name = {
        str(table.get("urn") or ""): str(table.get("tableName") or "")
        for table in tables
        if table.get("urn") and table.get("tableName")
    }
    urns = list(urn_to_name)[:max_tables]
    if not urns:
        return []

    query = """
    query datasetLineage($urn: String!) {
      dataset(urn: $urn) {
        upstreamLineage { entities { urn } }
        downstreamLineage { entities { urn } }
      }
    }
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    edges: list[dict[str, str]] = []
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for urn in urns:
            response = await client.post(
                f"{datahub_url.rstrip('/')}/api/graphql",
                headers=headers,
                json={"query": query, "variables": {"urn": urn}},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload["errors"]))
            dataset = (payload.get("data") or {}).get("dataset") or {}
            current = urn_to_name[urn]
            for entity in ((dataset.get("upstreamLineage") or {}).get("entities") or []):
                upstream = _table_name_for_urn(str(entity.get("urn") or ""), urn_to_name)
                if upstream and upstream != current:
                    edges.append({"upstream": upstream, "downstream": current})
            for entity in ((dataset.get("downstreamLineage") or {}).get("entities") or []):
                downstream = _table_name_for_urn(str(entity.get("urn") or ""), urn_to_name)
                if downstream and downstream != current:
                    edges.append({"upstream": current, "downstream": downstream})
    return _dedupe_edges(edges)


def _table_name_for_urn(urn: str, urn_to_name: dict[str, str]) -> str:
    if urn in urn_to_name:
        return urn_to_name[urn]
    identifier = _dataset_identifier(urn)
    parts = [part for part in identifier.split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return ""


def _dataset_identifier(urn: str) -> str:
    if "," not in urn:
        return urn
    return urn.split(",", 2)[1]


def _dedupe_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for edge in edges:
        key = (edge["upstream"], edge["downstream"])
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result
