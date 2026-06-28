from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    ascii_value = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    compact = re.sub(r"[^a-z0-9_]+", " ", ascii_value).strip()
    replacements = {
        "chi phi": "cost",
        "do tre": "latency",
        "loi": "error",
        "nguon": "source",
        "dich vu": "service",
        "mo hinh": "model",
        "xu huong": "trend",
        "bao cao": "report",
        "tong quan": "overview",
    }
    for source, target in replacements.items():
        compact = compact.replace(source, target)
    return compact


def table_name(table: dict[str, Any]) -> str:
    return str(table.get("tableName") or table.get("name") or "").strip()


def column_names(table: dict[str, Any]) -> list[str]:
    return [
        str(column.get("name") or "").strip()
        for column in table.get("columns") or []
        if str(column.get("name") or "").strip()
    ]


def compact_table(table: dict[str, Any], *, columns: list[str] | None = None) -> dict[str, Any]:
    allowed = set(columns or [])
    selected_columns = []
    for column in table.get("columns") or []:
        name = str(column.get("name") or "")
        if allowed and name not in allowed:
            continue
        selected_columns.append(
            {
                "name": name,
                "type": column.get("type"),
                "description": column.get("description") or "",
            }
        )
    return {
        "tableName": table_name(table),
        "database": table.get("database") or table.get("schema"),
        "description": table.get("description") or "",
        "columns": selected_columns,
        "retrievalScore": table.get("retrievalScore"),
        "retrievalConfidence": table.get("retrievalConfidence"),
        "retrievalReasons": table.get("retrievalReasons") or [],
    }
