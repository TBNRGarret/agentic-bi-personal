"""Shared DataHub entity-parsing helpers.

Both the MCP metadata client (``metadata.client.datahub_client``) and the
backend DataHub connector (``src.backend.app.connectors.datahub``) need to
parse the same GraphQL entity shapes.  This module is the single source of
truth for those transformations so the two call-sites stay in sync.
"""

from __future__ import annotations

from typing import Any


def custom_properties(properties: dict[str, Any] | None) -> dict[str, str]:
    """Extract ``customProperties`` map from a DataHub properties aspect."""
    if not properties:
        return {}
    output: dict[str, str] = {}
    for item in properties.get("customProperties") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            output[key] = str(item.get("value") or "")
    return output


def global_tags(global_tags_aspect: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalise a DataHub ``globalTags`` aspect into ``[{"tagFQN": ...}]``."""
    tags: list[dict[str, str]] = []
    for item in (global_tags_aspect or {}).get("tags") or []:
        tag = item.get("tag") if isinstance(item, dict) else None
        name = str((tag or {}).get("name") or "").strip()
        if name:
            tags.append({"tagFQN": name})
    return tags


def partition_info(props: dict[str, str]) -> dict[str, str] | None:
    """Derive ClickHouse partition metadata from custom-property conventions."""
    lowered = {k.lower(): v for k, v in props.items()}
    key = (
        lowered.get("partition_key")
        or lowered.get("partition_column")
        or lowered.get("partitionkey")
        or lowered.get("clickhouse.partition_key")
    )
    strategy = (
        lowered.get("partition_strategy")
        or lowered.get("partition_type")
        or lowered.get("partition_granularity")
        or lowered.get("clickhouse.partition_strategy")
    )
    expression = lowered.get("partition_expression") or lowered.get("clickhouse.partition_expression")
    if not key and expression:
        key = expression
    if not key:
        return None
    return {
        "key": str(key).strip(),
        "strategy": str(strategy or "monthly").strip().lower(),
        "expression": str(expression or "").strip(),
    }


def dataset_identifier(urn: str) -> str:
    """Extract the ``platform_instance.schema.table`` segment from a URN."""
    if "," not in urn:
        return urn
    return urn.split(",", 2)[1]


def description(entity: dict[str, Any]) -> str:
    """Prefer editable description, fall back to properties description."""
    return (
        (entity.get("editableProperties") or {}).get("description")
        or (entity.get("properties") or {}).get("description")
        or ""
    )


def ms_to_iso(ms: int) -> str:
    """Convert epoch-milliseconds to ISO-8601 UTC string."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
