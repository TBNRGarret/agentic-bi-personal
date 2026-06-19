from __future__ import annotations

from datetime import date, datetime
from numbers import Number
import re
from typing import Any


def infer_chart_spec(
    rows: list[dict[str, Any]],
    intent: str | None = None,
    requested_type: str | None = None,
    planned_spec: dict[str, Any] | None = None,
    preferred_y: list[str] | None = None,
    requested_dimensions: list[str] | None = None,
) -> dict[str, Any]:
    if not rows:
        return {"type": "table", "reason": "No rows returned"}
    columns = list(rows[0])
    numeric = [
        column
        for column in columns
        if _is_numeric_column(rows, column)
    ]
    temporal = [
        column
        for column in columns
        if any(token in column.lower() for token in ("date", "time", "day", "hour"))
        or _looks_temporal(rows, column)
    ]
    categorical = [column for column in columns if column not in numeric]
    preferred_numeric = [
        column for column in (preferred_y or []) if column in numeric
    ]
    requested_dimension_columns = _requested_dimension_columns(
        requested_dimensions or [],
        columns,
        temporal,
    )
    planned = _validated_planned_spec(
        planned_spec,
        rows=rows,
        columns=columns,
        numeric=numeric,
        temporal=temporal,
        categorical=categorical,
        preferred_numeric=preferred_numeric,
        requested_type=requested_type,
        requested_dimension_columns=requested_dimension_columns,
    )
    if planned:
        return planned
    explicit = _explicit_chart_spec(
        requested_type,
        rows=rows,
        columns=columns,
        numeric=numeric,
        temporal=temporal,
        categorical=categorical,
        preferred_numeric=preferred_numeric,
        requested_dimension_columns=requested_dimension_columns,
    )
    if explicit:
        return explicit
    if len(rows) == 1 or not numeric or (intent == "lookup" and len(rows) > 50):
        return {
            "type": "table",
            "columns": columns,
            "reason": "Lookup-shaped result detected",
        }
    if temporal and numeric:
        x = _preferred_temporal_x(temporal, requested_dimension_columns)
        series = _series_for_dimensions(
            requested_dimension_columns,
            x=x,
            y_columns=preferred_numeric or numeric,
            categorical=categorical,
        ) or _series_for_repeated_x(
            rows,
            x=x,
            y_columns=preferred_numeric or numeric,
            categorical=categorical,
        )
        return {
            "type": "line",
            "x": x,
            "y": (preferred_numeric or numeric)[:3],
            **({"series": _series_value(series)} if series else {}),
            "reason": "Time dimension and numeric metrics detected",
        }
    if categorical and len(numeric) == 1 and 2 <= len(rows) <= 8:
        values = [row.get(numeric[0]) for row in rows]
        if all(isinstance(value, Number) and value >= 0 for value in values):
            return {
                "type": "pie",
                "x": categorical[0],
                "y": numeric[0],
                "reason": "Small categorical share result detected",
            }
    if categorical and numeric:
        x = _preferred_categorical_x(categorical, requested_dimension_columns)
        series = _series_for_dimensions(
            requested_dimension_columns,
            x=x,
            y_columns=numeric,
            categorical=categorical,
        ) or _series_for_repeated_x(
            rows,
            x=x,
            y_columns=numeric,
            categorical=categorical,
        )
        return {
            "type": "bar",
            "x": x,
            "y": numeric[:3],
            **({"series": _series_value(series)} if series else {}),
            "reason": "Category and numeric metrics detected",
        }
    return {
        "type": "table",
        "columns": columns,
        "reason": "No compatible chart shape detected",
    }


def _looks_temporal(rows: list[dict[str, Any]], column: str) -> bool:
    values = [row.get(column) for row in rows[:12] if row.get(column) is not None]
    if not values:
        return False
    matches = 0
    for value in values:
        if isinstance(value, (date, datetime)):
            matches += 1
        elif isinstance(value, str) and re.match(
            r"^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$", value.strip()
        ):
            matches += 1
    return matches / len(values) >= 0.8


def _validated_planned_spec(
    planned_spec: dict[str, Any] | None,
    *,
    rows: list[dict[str, Any]],
    columns: list[str],
    numeric: list[str],
    temporal: list[str],
    categorical: list[str],
    preferred_numeric: list[str],
    requested_type: str | None,
    requested_dimension_columns: list[str],
) -> dict[str, Any] | None:
    if not isinstance(planned_spec, dict):
        return None
    chart_type = str(planned_spec.get("type") or "").lower()
    if chart_type not in {
        "table",
        "line",
        "bar",
        "pie",
        "scatter",
        "histogram",
        "box",
        "heatmap",
    }:
        return None
    if requested_type and chart_type != requested_type:
        return None

    x = str(planned_spec.get("x") or "")
    raw_y = planned_spec.get("y")
    if isinstance(raw_y, list):
        y_columns = [str(item) for item in raw_y if str(item) in columns][:4]
    elif raw_y is not None and str(raw_y) in columns:
        y_columns = [str(raw_y)]
    else:
        y_columns = []
    if preferred_numeric and not any(
        column in preferred_numeric for column in y_columns
    ):
        return None
    series_columns = _series_columns(planned_spec.get("series"), columns)
    if x:
        for column in requested_dimension_columns:
            if (
                column != x
                and column in categorical
                and column not in series_columns
                and column not in y_columns
            ):
                series_columns.append(column)
    title = str(planned_spec.get("title") or "").strip()
    rationale = str(planned_spec.get("rationale") or "").strip()
    reason = (
        f"LLM visualization plan validated: {rationale}"
        if rationale
        else "LLM visualization plan validated against returned rows"
    )

    if chart_type == "table":
        return {
            "type": "table",
            "columns": columns,
            "title": title or None,
            "reason": reason,
            "source": "llm_validated",
        }
    if chart_type == "pie":
        pie_x = x or (series_columns[0] if series_columns else "")
        pie_y = y_columns[0] if y_columns else _best_pie_numeric(numeric)
        if (
            pie_x in categorical
            and pie_y in numeric
            and len(rows) >= 2
            and all(
                isinstance(row.get(pie_y), Number)
                and row.get(pie_y) >= 0
                for row in rows
            )
        ):
            return _planned_result(
                chart_type, pie_x, pie_y, "", title, reason
            )
        return None
    if chart_type == "line":
        if x in temporal and y_columns and all(
            column in numeric for column in y_columns
        ):
            return _planned_result(
                chart_type, x, y_columns, series_columns, title, reason
            )
        return None
    if chart_type in {"bar", "heatmap"}:
        if x in columns and y_columns and all(
            column in numeric for column in y_columns
        ):
            return _planned_result(
                chart_type, x, y_columns, series_columns, title, reason
            )
        return None
    if chart_type == "scatter":
        if x in numeric and len(y_columns) == 1 and y_columns[0] in numeric:
            return _planned_result(
                chart_type, x, y_columns[0], series_columns, title, reason
            )
        return None
    if chart_type in {"histogram", "box"}:
        if y_columns and all(column in numeric for column in y_columns):
            return _planned_result(
                chart_type,
                x if x in columns else y_columns[0],
                y_columns,
                series_columns,
                title,
                reason,
            )
    return None


def _planned_result(
    chart_type: str,
    x: str,
    y: str | list[str],
    series: str | list[str],
    title: str,
    reason: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": chart_type,
        "x": x,
        "y": y,
        "reason": reason,
        "source": "llm_validated",
    }
    series_value = _series_value(series if isinstance(series, list) else [series])
    if series_value:
        result["series"] = series_value
    if title:
        result["title"] = title
    return result


def _explicit_chart_spec(
    requested_type: str | None,
    *,
    rows: list[dict[str, Any]],
    columns: list[str],
    numeric: list[str],
    temporal: list[str],
    categorical: list[str],
    preferred_numeric: list[str],
    requested_dimension_columns: list[str],
) -> dict[str, Any] | None:
    if not requested_type:
        return None
    if requested_type == "table":
        return {
            "type": "table",
            "columns": columns,
            "reason": "The user explicitly requested a table",
        }
    if requested_type == "pie":
        best_num = _best_pie_numeric(numeric)
        values = [row.get(best_num) for row in rows] if best_num else []
        pie_categories = [
            column for column in categorical if column not in temporal
        ] or categorical
        if pie_categories and best_num and len(rows) >= 2 and all(
            isinstance(value, Number) and value >= 0 for value in values
        ):
            return {
                "type": "pie",
                "x": pie_categories[0],
                "y": best_num,
                "reason": "The user explicitly requested a pie chart",
            }
    if requested_type == "line" and temporal and numeric:
        x = _preferred_temporal_x(temporal, requested_dimension_columns)
        series = _series_for_dimensions(
            requested_dimension_columns,
            x=x,
            y_columns=preferred_numeric or numeric,
            categorical=categorical,
        ) or _series_for_repeated_x(
            rows,
            x=x,
            y_columns=preferred_numeric or numeric,
            categorical=categorical,
        )
        return {
            "type": "line",
            "x": x,
            "y": (preferred_numeric or numeric)[:3],
            **({"series": _series_value(series)} if series else {}),
            "reason": "The user explicitly requested a line chart",
        }
    if requested_type == "bar" and categorical and numeric:
        x = _preferred_categorical_x(categorical, requested_dimension_columns)
        series = _series_for_dimensions(
            requested_dimension_columns,
            x=x,
            y_columns=preferred_numeric or numeric,
            categorical=categorical,
        ) or _series_for_repeated_x(
            rows,
            x=x,
            y_columns=preferred_numeric or numeric,
            categorical=categorical,
        )
        return {
            "type": "bar",
            "x": x,
            "y": (preferred_numeric or numeric)[:3],
            **({"series": _series_value(series)} if series else {}),
            "reason": "The user explicitly requested a bar chart",
        }
    if requested_type == "scatter" and len(numeric) >= 2:
        return {
            "type": "scatter",
            "x": numeric[0],
            "y": numeric[1],
            "reason": "The user explicitly requested a scatter chart",
        }
    if requested_type in {"histogram", "box"} and numeric:
        return {
            "type": requested_type,
            "x": categorical[0] if categorical else numeric[0],
            "y": numeric[:3],
            "reason": f"The user explicitly requested a {requested_type} chart",
        }
    return {
        "type": "table",
        "columns": columns,
        "reason": (
            f"The requested {requested_type} chart is incompatible with "
            "the returned columns"
        ),
    }


def _best_pie_numeric(numeric: list[str]) -> str | None:
    if not numeric:
        return None
    if len(numeric) == 1:
        return numeric[0]
    for term in ("cost", "revenue", "amount", "total", "count", "sum"):
        for col in numeric:
            if term in col.lower():
                return col
    return numeric[0]


def _is_numeric_column(rows: list[dict[str, Any]], column: str) -> bool:
    values = [row.get(column) for row in rows[:25] if row.get(column) is not None]
    if not values:
        return False
    matches = 0
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, Number):
            matches += 1
            continue
        if isinstance(value, str) and value.strip():
            try:
                float(value)
            except ValueError:
                continue
            matches += 1
    return matches / len(values) >= 0.8


def _requested_dimension_columns(
    dimensions: list[str],
    columns: list[str],
    temporal: list[str],
) -> list[str]:
    aliases = {
        "time": [
            *temporal,
            "metric_date",
            "date",
            "event_time",
            "timestamp",
            "started_at",
            "created_at",
        ],
        "model": ["model"],
        "source_app": ["source_app", "source_app_id", "app"],
        "service_name": ["service_name", "service"],
        "provider": ["provider"],
        "environment": ["environment", "source_env"],
        "status": ["status", "generation_status", "span_status"],
        "user": ["user", "user_id", "username"],
    }
    result: list[str] = []
    lower_to_column = {column.lower(): column for column in columns}
    for dimension in dimensions:
        for candidate in aliases.get(str(dimension), [str(dimension)]):
            column = lower_to_column.get(candidate.lower())
            if column and column not in result:
                result.append(column)
                break
    return result


def _series_columns(value: Any, columns: list[str]) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for raw in raw_values:
        column = str(raw or "").strip()
        if column in columns and column not in result:
            result.append(column)
    return result


def _preferred_temporal_x(
    temporal: list[str],
    requested_dimension_columns: list[str],
) -> str:
    for column in requested_dimension_columns:
        if column in temporal:
            return column
    return temporal[0]


def _preferred_categorical_x(
    categorical: list[str],
    requested_dimension_columns: list[str],
) -> str:
    for column in requested_dimension_columns:
        if column in categorical:
            return column
    return categorical[0]


def _series_for_dimensions(
    requested_dimension_columns: list[str],
    *,
    x: str,
    y_columns: list[str],
    categorical: list[str],
) -> list[str]:
    return [
        column
        for column in requested_dimension_columns
        if column != x
        and column in categorical
        and column not in y_columns
    ]


def _series_for_repeated_x(
    rows: list[dict[str, Any]],
    *,
    x: str,
    y_columns: list[str],
    categorical: list[str],
) -> list[str]:
    candidates = [
        column
        for column in categorical
        if column != x and column not in y_columns
    ]
    if not candidates:
        return []

    x_counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(x) or "")
        x_counts[key] = x_counts.get(key, 0) + 1
    repeated_x = {key for key, count in x_counts.items() if key and count > 1}
    if not repeated_x:
        return []

    useful: list[str] = []
    max_unique_values = max(2, min(24, len(rows) // 2 or 2))
    for column in candidates:
        values = {str(row.get(column) or "") for row in rows if row.get(column) is not None}
        values.discard("")
        if not (1 < len(values) <= max_unique_values):
            continue
        splits_repeated_x = any(
            len(
                {
                    str(row.get(column) or "")
                    for row in rows
                    if str(row.get(x) or "") == x_value and row.get(column) is not None
                }
            ) > 1
            for x_value in repeated_x
        )
        if splits_repeated_x:
            useful.append(column)
    return useful[:2]

def _series_value(series: list[str]) -> str | list[str] | None:
    cleaned = [column for column in series if column]
    if not cleaned:
        return None
    return cleaned[0] if len(cleaned) == 1 else cleaned
