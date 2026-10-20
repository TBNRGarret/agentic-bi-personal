from __future__ import annotations

from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from ..security import Principal


class RowSecurityError(ValueError):
    pass


def apply_row_security(
    sql: str,
    *,
    dialect: str,
    tables: list[dict[str, Any]],
    principal: Principal,
) -> str:
    table_columns = {
        str(table.get("tableName") or "").lower(): {
            str(column.get("name") or "").lower()
            for column in table.get("columns") or []
        }
        for table in tables
    }
    bare_to_qualified = {
        t_name.split(".")[-1]: t_name for t_name in table_columns
    }

    try:
        expression = parse_one(sql, read=dialect)
    except SqlglotError as exc:
        raise RowSecurityError(f"Unable to parse SQL for row-level policy: {exc}") from exc

    table_nodes = list(expression.find_all(exp.Table))
    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}

    # Validate that every physical table exists in visible schema (even in mixed queries)
    for table in table_nodes:
        if not table.db and table.name.lower() in cte_names:
            continue
        qualified = ".".join(
            part for part in (table.db, table.name) if part
        ).lower()
        if qualified not in table_columns and qualified in bare_to_qualified:
            qualified = bare_to_qualified[qualified]
        if qualified not in table_columns:
            raise RowSecurityError(
                f"Query references a table outside the visible schema: {qualified or table.name}"
            )

    if principal.is_admin:
        return sql

    matched_tables = 0
    unscoped_tables: list[str] = []
    select_predicates: dict[int, tuple[exp.Select, list[exp.Expression]]] = {}

    for table in table_nodes:
        if not table.db and table.name.lower() in cte_names:
            continue
        qualified = ".".join(
            part for part in (table.db, table.name) if part
        ).lower()
        if qualified not in table_columns and qualified in bare_to_qualified:
            qualified = bare_to_qualified[qualified]
        columns = table_columns.get(qualified)
        if columns is None:
            continue
        matched_tables += 1

        predicates: list[exp.Expression] = []
        alias = table.alias_or_name
        if "service_name" in columns:
            predicates.append(
                _allowed_predicate(
                    exp.column("service_name", table=alias),
                    principal.allowed_services,
                )
            )
        if "source_app" in columns:
            predicates.append(
                _allowed_predicate(
                    exp.column("source_app", table=alias),
                    principal.allowed_source_apps,
                )
            )
        if "source_app_id" in columns:
            predicates.append(
                _allowed_predicate(
                    exp.column("source_app_id", table=alias),
                    principal.allowed_source_apps,
                )
            )
        if not predicates:
            unscoped_tables.append(qualified)
            continue

        # Check if table is on the right/inner side of an outer join
        join = table.find_ancestor(exp.Join)
        if join and (join.side or "").upper() in ("LEFT", "RIGHT", "FULL"):
            combined = predicates[0]
            for item in predicates[1:]:
                combined = exp.and_(combined, item)
            existing_on = join.args.get("on")
            if existing_on:
                join.set("on", exp.and_(existing_on, combined))
            else:
                join.set("on", combined)
            continue

        select = table.find_ancestor(exp.Select)
        if select is None:
            continue
        _, collected = select_predicates.setdefault(id(select), (select, []))
        collected.extend(predicates)

    if unscoped_tables:
        raise RowSecurityError(
            "No row-level policy is defined for: "
            + ", ".join(sorted(set(unscoped_tables)))
        )
    if table_nodes and matched_tables == 0:
        raise RowSecurityError("Query references a table outside the visible schema")

    for select, predicates in select_predicates.values():
        predicate = predicates[0]
        for item in predicates[1:]:
            predicate = exp.and_(predicate, item)
        select.where(predicate, append=True, copy=False)
    return expression.sql(dialect=dialect)


def visible_tables(
    tables: list[dict[str, Any]], principal: Principal
) -> list[dict[str, Any]]:
    if principal.is_admin:
        return tables
    return [
        table
        for table in tables
        if str(table.get("database") or "")
        in {"vsf_mart", "vsf_raw"}
    ]


def _allowed_predicate(
    column: exp.Column, allowed: tuple[str, ...]
) -> exp.Expression:
    if not allowed:
        return exp.false()
    return column.isin(*[exp.Literal.string(value) for value in allowed])
