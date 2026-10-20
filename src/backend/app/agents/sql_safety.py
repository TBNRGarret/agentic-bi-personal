from __future__ import annotations

import re
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from ..security import Principal


class SqlSafetyError(ValueError):
    pass


FORBIDDEN_AST_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Kill,
    exp.TruncateTable,
)


def validate_readonly_sql(sql: str, limit: int = 100, dialect: str = "clickhouse") -> str:
    """Validate that SQL is a read-only single query and enforce an outer LIMIT."""
    candidate = str(sql or "").strip().rstrip(";")
    if not candidate:
        raise SqlSafetyError("SQL is empty")
    if len(candidate) > 8000:
        raise SqlSafetyError("SQL exceeds the 8000 character limit")

    try:
        # Check for multiple statements
        import sqlglot
        parsed = sqlglot.parse(candidate, read=dialect)
    except Exception as exc:
        raise SqlSafetyError(f"Unable to parse SQL: {exc}") from exc

    if len(parsed) > 1:
        raise SqlSafetyError("Multiple statements are not allowed")
    if not parsed or parsed[0] is None:
        raise SqlSafetyError("SQL is empty")

    expr = parsed[0]

    # Validate AST does not contain forbidden DDL/DML operations
    if isinstance(expr, FORBIDDEN_AST_NODES) or any(
        isinstance(node, FORBIDDEN_AST_NODES) for node in expr.walk()
    ):
        raise SqlSafetyError("Only read-only SELECT statements are allowed")

    if not isinstance(expr, (exp.Select, exp.Union)):
        raise SqlSafetyError("SQL must start with SELECT or WITH")

    # Enforce limit: min(requested limit, 500, existing limit if present)
    capped_limit = max(1, min(int(limit), 500))

    if isinstance(expr, exp.Select):
        existing_limit = expr.args.get("limit")
        if existing_limit is not None:
            try:
                curr_val = int(str(existing_limit.expression))
                if curr_val > capped_limit:
                    existing_limit.set("expression", exp.Literal.number(capped_limit))
            except (ValueError, TypeError):
                existing_limit.set("expression", exp.Literal.number(capped_limit))
        else:
            expr.set("limit", exp.Limit(expression=exp.Literal.number(capped_limit)))
    elif isinstance(expr, exp.Union):
        existing_limit = expr.args.get("limit")
        if existing_limit is not None:
            try:
                curr_val = int(str(existing_limit.expression))
                if curr_val > capped_limit:
                    existing_limit.set("expression", exp.Literal.number(capped_limit))
            except (ValueError, TypeError):
                existing_limit.set("expression", exp.Literal.number(capped_limit))
        else:
            expr = exp.select("*").from_(expr.subquery("_union_stmt")).limit(capped_limit)

    return expr.sql(dialect=dialect)


def validate_known_tables(
    sql: str,
    tables: list[dict[str, Any]],
    dialect: str = "clickhouse",
) -> None:
    """Validate that every physical table referenced in the SQL exists in tables."""
    allowed = {str(table.get("tableName") or "").lower() for table in tables}
    allowed_bare = {name.split(".")[-1] for name in allowed}

    try:
        expression = parse_one(sql, read=dialect)
    except SqlglotError as exc:
        raise SqlSafetyError(f"Unable to parse SQL for table validation: {exc}") from exc

    table_nodes = list(expression.find_all(exp.Table))
    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}

    for table in table_nodes:
        if not table.db and table.name.lower() in cte_names:
            continue
        qualified = ".".join(
            part for part in (table.db, table.name) if part
        ).lower()
        if table.db:
            if qualified not in allowed:
                raise ValueError(f"SQL references table outside live metadata: {qualified}")
        else:
            if qualified not in allowed and qualified not in allowed_bare:
                raise ValueError(f"SQL references table outside live metadata: {qualified}")


def _referenced_table_names(
    sql: str,
    tables: list[dict[str, Any]],
    dialect: str = "clickhouse",
) -> list[str]:
    """Extract canonical table names referenced in SQL."""
    allowed = {
        str(table.get("tableName") or "").lower(): str(table.get("tableName") or "")
        for table in tables
    }
    allowed_bare = {
        name.split(".")[-1]: canonical for name, canonical in allowed.items()
    }
    names: list[str] = []

    try:
        expression = parse_one(sql, read=dialect)
        table_nodes = list(expression.find_all(exp.Table))
        cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
        for table in table_nodes:
            if not table.db and table.name.lower() in cte_names:
                continue
            qualified = ".".join(
                part for part in (table.db, table.name) if part
            ).lower()
            canonical = allowed.get(qualified) or allowed_bare.get(qualified)
            if canonical and canonical not in names:
                names.append(canonical)
        return names
    except Exception:
        # Regex fallback
        for reference in re.findall(
            r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w.]*)", sql, re.IGNORECASE
        ):
            canonical = allowed.get(reference.lower()) or allowed_bare.get(reference.lower())
            if canonical and canonical not in names:
                names.append(canonical)
        return names


def prepare_sql_candidate(
    sql: str,
    *,
    limit: int = 100,
    datasource: str = "clickhouse",
    tables: list[dict[str, Any]] | None = None,
    principal: Principal | None = None,
) -> str:
    """Validate read-only safety, verify allowed tables, and apply row security."""
    dialect = "postgres" if datasource.lower() in {"dwh", "postgres", "postgresql"} else "clickhouse"
    candidate = validate_readonly_sql(sql, limit=limit, dialect=dialect)

    if tables is not None:
        validate_known_tables(candidate, tables, dialect=dialect)

    if principal is not None and tables is not None:
        from .row_security import apply_row_security
        candidate = apply_row_security(
            candidate,
            dialect=dialect,
            tables=tables,
            principal=principal,
        )
        candidate = validate_readonly_sql(candidate, limit=limit, dialect=dialect)

    return candidate


# Aliases for backwards compatibility
_prepare_sql_candidate = prepare_sql_candidate
_validate_known_tables = validate_known_tables
