#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {body}") from exc


def login(base_url: str, username: str, password: str) -> str:
    result = request_json(
        "POST",
        f"{base_url.rstrip('/')}/api/auth/login",
        {"username": username, "password": password},
    )
    token = str(result.get("accessToken") or "")
    if not token:
        raise RuntimeError("Login succeeded but accessToken is missing.")
    return token


def safe_table_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", name))


def query_clickhouse_via_backend(
    base_url: str,
    sql: str,
    *,
    token: str | None,
    limit: int = 20,
) -> dict[str, Any]:
    return request_json(
        "POST",
        f"{base_url.rstrip('/')}/api/query/dwh",
        {
            "sql": sql,
            "target": "clickhouse",
            "limit": limit,
        },
        token=token,
    )


def get_schema(
    base_url: str,
    *,
    token: str | None,
    datasource: str = "clickhouse",
    refresh: bool = True,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"datasource": datasource, "refresh": str(refresh).lower()}
    )
    return request_json(
        "GET",
        f"{base_url.rstrip('/')}/api/query/schema?{query}",
        token=token,
    )


def print_health(base_url: str) -> None:
    print("=== HEALTH ===")
    try:
        health = request_json("GET", f"{base_url.rstrip('/')}/health")
        print(json.dumps(health, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        print(f"Cannot read /health: {exc}")
    print("")


def inspect_tables(
    base_url: str,
    tables: list[dict[str, Any]],
    *,
    token: str | None,
    max_tables: int,
    sample_rows: int,
) -> None:
    print("=== CLICKHOUSE REAL DATA CHECK ===")
    checked = 0

    for table in tables:
        table_name = str(table.get("tableName") or "")
        if not safe_table_name(table_name):
            print(f"- SKIP unsafe table name: {table_name}")
            continue

        columns = table.get("columns") or []
        column_names = [str(column.get("name") or "") for column in columns]
        print(f"\n--- {table_name} ---")
        print(f"columns: {len(column_names)}")
        print(f"firstColumns: {column_names[:12]}")

        try:
            count_result = query_clickhouse_via_backend(
                base_url,
                f"SELECT count() AS row_count FROM {table_name}",
                token=token,
                limit=1,
            )
            rows = count_result.get("rows") or []
            row_count = rows[0].get("row_count") if rows else None
            print(f"row_count: {row_count}")
        except Exception as exc:
            print(f"count_error: {exc}")
            checked += 1
            if checked >= max_tables:
                break
            continue

        if sample_rows > 0:
            try:
                sample_result = query_clickhouse_via_backend(
                    base_url,
                    f"SELECT * FROM {table_name} LIMIT {sample_rows}",
                    token=token,
                    limit=sample_rows,
                )
                sample = sample_result.get("rows") or []
                print(f"sample_rows: {len(sample)}")
                for index, row in enumerate(sample[:sample_rows], start=1):
                    print(f"  row[{index}]: {json.dumps(row, ensure_ascii=False, default=str)[:1000]}")
            except Exception as exc:
                print(f"sample_error: {exc}")

        checked += 1
        if checked >= max_tables:
            break


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect metadata DataHub và real data ClickHouse mà agent dùng."
    )
    parser.add_argument("--base-url", default="http://47.131.228.222:3003")
    parser.add_argument("--token", default="")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="pt2tCF8QBokuUix+0IOoUiCa")
    parser.add_argument("--max-tables", type=int, default=20)
    parser.add_argument("--sample-rows", type=int, default=2)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    token = args.token or None
    if not token and args.username and args.password:
        token = login(args.base_url, args.username, args.password)

    print_health(args.base_url)

    print("=== DATAHUB / METADATA CATALOG ===")
    schema = get_schema(
        args.base_url,
        token=token,
        datasource="clickhouse",
        refresh=args.refresh,
    )
    tables = schema.get("tables") or []
    print(f"schemaSource: {schema.get('source')}")
    print(f"tableCount:   {schema.get('tableCount')}")
    print(f"generatedAt:  {schema.get('generatedAt')}")
    print("")

    for table in tables[: args.max_tables]:
        columns = table.get("columns") or []
        print(
            f"- {table.get('tableName')} "
            f"database={table.get('database')} "
            f"datasource={table.get('datasource')} "
            f"columns={len(columns)}"
        )

    inspect_tables(
        args.base_url,
        tables,
        token=token,
        max_tables=args.max_tables,
        sample_rows=args.sample_rows,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)