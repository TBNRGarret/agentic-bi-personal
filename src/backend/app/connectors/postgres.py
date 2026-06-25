from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from ..config import Settings


class PostgresConnector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def health(self) -> bool:
        try:
            rows = await self.query("SELECT 1 AS ok")
            return bool(rows)
        except Exception:
            return False

    async def ensure_agentic_schema(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "migrations" / "agentic_schema.sql"
        await self.execute(migration.read_text(encoding="utf-8"))

    async def query(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._query_sync, sql, params)

    async def describe_tables(
        self, schemas: tuple[str, ...] = ("raw", "ops")
    ) -> list[dict[str, Any]]:
        rows = await self.query(
            """
            SELECT table_schema, table_name, column_name, data_type,
                   ordinal_position
            FROM information_schema.columns
            WHERE table_schema = ANY(%s)
            ORDER BY table_schema, table_name, ordinal_position
            """,
            (list(schemas),),
        )
        tables: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = f"{row['table_schema']}.{row['table_name']}"
            table = tables.setdefault(
                key,
                {
                    "tableName": key,
                    "database": row["table_schema"],
                    "name": row["table_name"],
                    "datasource": "dwh",
                    "columns": [],
                },
            )
            table["columns"].append(
                {"name": row["column_name"], "type": row["data_type"]}
            )
        return list(tables.values())

    async def execute(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> None:
        await asyncio.to_thread(self._execute_sync, sql, params)

    async def write_query(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._write_query_sync, sql, params)

    async def get_user(self, username: str) -> dict[str, Any] | None:
        rows = await self.query(
            """
            SELECT user_id, username, password_hash, role, team,
                   allowed_services, allowed_source_apps, is_active
            FROM auth.users
            WHERE lower(username) = lower(%s)
            LIMIT 1
            """,
            (username,),
        )
        return rows[0] if rows else None

    async def list_users(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.query(
            """
            SELECT user_id, username, role, team, allowed_services,
                   allowed_source_apps, is_active, created_at, updated_at
            FROM auth.users
            ORDER BY username
            LIMIT %s
            """,
            (min(max(limit, 1), 500),),
        )

    async def upsert_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str,
        team: str,
        allowed_services: list[str],
        allowed_source_apps: list[str],
        preserve_existing_password: bool = False,
    ) -> dict[str, Any]:
        rows = await self.write_query(
            """
            INSERT INTO auth.users (
                user_id, username, password_hash, role, team,
                allowed_services, allowed_source_apps
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = CASE
                    WHEN %s THEN auth.users.password_hash
                    ELSE EXCLUDED.password_hash
                END,
                role = EXCLUDED.role,
                team = EXCLUDED.team,
                allowed_services = EXCLUDED.allowed_services,
                allowed_source_apps = EXCLUDED.allowed_source_apps,
                updated_at = NOW()
            RETURNING user_id, username, role, team, allowed_services,
                      allowed_source_apps, is_active, created_at, updated_at
            """,
            (
                uuid4().hex,
                username,
                password_hash,
                role,
                team,
                allowed_services,
                allowed_source_apps,
                preserve_existing_password,
            ),
        )
        return rows[0]

    async def create_chat_session(
        self, user_id: str, title: str
    ) -> dict[str, Any]:
        session_id = uuid4().hex
        rows = await self.write_query(
            """
            INSERT INTO ops.chat_sessions (session_id, user_id, title)
            VALUES (%s, %s, %s)
            RETURNING session_id, title, structured_memory, created_at, updated_at
            """,
            (session_id, user_id, title[:160]),
        )
        return rows[0]

    async def list_chat_sessions(
        self, user_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        return await self.query(
            """
            SELECT session_id, title, structured_memory, created_at, updated_at
            FROM ops.chat_sessions
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (user_id, min(limit, 100)),
        )

    async def get_chat_session(
        self, session_id: str, user_id: str
    ) -> dict[str, Any] | None:
        sessions = await self.query(
            """
            SELECT session_id, title, structured_memory, created_at, updated_at
            FROM ops.chat_sessions
            WHERE session_id = %s AND user_id = %s
            """,
            (session_id, user_id),
        )
        if not sessions:
            return None
        messages = await self.query(
            """
            SELECT message_id, role, content, payload, structured_state, created_at
            FROM ops.chat_messages
            WHERE session_id = %s AND user_id = %s
            ORDER BY message_id
            LIMIT 200
            """,
            (session_id, user_id),
        )
        return {**sessions[0], "messages": messages}

    async def append_chat_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        question: str,
        answer: str,
        payload: dict[str, Any],
        structured_memory: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._append_chat_turn_sync,
            session_id,
            user_id,
            question,
            answer,
            payload,
            structured_memory,
        )

    async def agent_cost_summary(
        self,
        *,
        user_id: str | None,
        days: int = 30,
        top_limit: int = 10,
    ) -> dict[str, Any]:
        user_filter = "AND user_id = %s" if user_id else ""
        base_params: tuple[Any, ...] = (days, user_id) if user_id else (days,)
        daily = await self.query(
            f"""
            SELECT
                created_at::date AS metric_date,
                COALESCE(NULLIF(token_usage->>'model', ''), 'unknown') AS model,
                count(*) FILTER (
                    WHERE COALESCE((token_usage->>'totalTokens')::bigint, 0) > 0
                ) AS query_count,
                COALESCE(sum((token_usage->>'promptTokens')::bigint), 0) AS prompt_tokens,
                COALESCE(sum((token_usage->>'completionTokens')::bigint), 0) AS completion_tokens,
                COALESCE(sum((token_usage->>'totalTokens')::bigint), 0) AS total_tokens,
                round(COALESCE(sum((token_usage->>'estimatedCostUsd')::numeric), 0), 8)
                    AS estimated_cost_usd
            FROM ops.agent_query_audit
            WHERE created_at >= CURRENT_DATE - %s
              AND token_usage <> '{{}}'::jsonb
              {user_filter}
            GROUP BY created_at::date, model
            ORDER BY metric_date, estimated_cost_usd DESC
            """,
            base_params,
        )
        top_params: tuple[Any, ...] = (
            (days, user_id, top_limit) if user_id else (days, top_limit)
        )
        top_queries = await self.query(
            f"""
            SELECT query_id, username, question, endpoint, created_at,
                   COALESCE((token_usage->>'totalTokens')::bigint, 0) AS total_tokens,
                   COALESCE((token_usage->>'estimatedCostUsd')::numeric, 0)
                       AS estimated_cost_usd,
                   token_usage->>'model' AS model
            FROM ops.agent_query_audit
            WHERE created_at >= CURRENT_DATE - %s
              AND token_usage <> '{{}}'::jsonb
              {user_filter}
            ORDER BY estimated_cost_usd DESC, total_tokens DESC
            LIMIT %s
            """,
            top_params,
        )
        return {"daily": daily, "topQueries": top_queries}

    async def audit(self, record: dict[str, Any]) -> None:
        await self.execute(
            """
            INSERT INTO ops.agent_query_audit (
                query_id, endpoint, request_type, question, generated_sql,
                target, status, row_count, execution_time_ms, selected_tables,
                attempts, token_usage, error_code, error_message, metadata,
                user_id, username, user_role, user_team
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb,
                %s, %s, %s, %s
            )
            ON CONFLICT (query_id) DO UPDATE SET
                generated_sql = EXCLUDED.generated_sql,
                target = EXCLUDED.target,
                status = EXCLUDED.status,
                row_count = EXCLUDED.row_count,
                execution_time_ms = EXCLUDED.execution_time_ms,
                selected_tables = EXCLUDED.selected_tables,
                attempts = EXCLUDED.attempts,
                token_usage = EXCLUDED.token_usage,
                error_code = EXCLUDED.error_code,
                error_message = EXCLUDED.error_message,
                metadata = EXCLUDED.metadata,
                user_id = EXCLUDED.user_id,
                username = EXCLUDED.username,
                user_role = EXCLUDED.user_role,
                user_team = EXCLUDED.user_team
            """,
            (
                record.get("query_id"),
                record.get("endpoint"),
                record.get("request_type"),
                record.get("question"),
                record.get("generated_sql"),
                record.get("target"),
                record.get("status"),
                record.get("row_count"),
                record.get("execution_time_ms"),
                json.dumps(record.get("selected_tables") or []),
                json.dumps(record.get("attempts") or []),
                json.dumps(record.get("token_usage") or {}),
                record.get("error_code"),
                record.get("error_message"),
                json.dumps(record.get("metadata") or {}),
                record.get("user_id"),
                record.get("username"),
                record.get("user_role"),
                record.get("user_team"),
            ),
        )

    def _query_sync(
        self, sql: str, params: tuple[Any, ...] | None
    ) -> list[dict[str, Any]]:
        with psycopg.connect(
            self.settings.dwh_postgres_dsn, row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(round(self.settings.postgres_query_timeout_seconds * 1000)),),
                )
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def _execute_sync(
        self, sql: str, params: tuple[Any, ...] | None
    ) -> None:
        with psycopg.connect(self.settings.dwh_postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
            connection.commit()

    def _write_query_sync(
        self, sql: str, params: tuple[Any, ...] | None
    ) -> list[dict[str, Any]]:
        with psycopg.connect(
            self.settings.dwh_postgres_dsn, row_factory=dict_row
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = [dict(row) for row in cursor.fetchall()]
            connection.commit()
            return rows

    def _append_chat_turn_sync(
        self,
        session_id: str,
        user_id: str,
        question: str,
        answer: str,
        payload: dict[str, Any],
        structured_memory: dict[str, Any],
    ) -> None:
        with psycopg.connect(self.settings.dwh_postgres_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ops.chat_messages (
                        session_id, user_id, role, content, structured_state
                    )
                    VALUES (%s, %s, 'user', %s, %s::jsonb)
                    """,
                    (
                        session_id,
                        user_id,
                        question,
                        json.dumps(structured_memory),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO ops.chat_messages (
                        session_id, user_id, role, content, payload,
                        structured_state
                    )
                    VALUES (%s, %s, 'assistant', %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        session_id,
                        user_id,
                        answer,
                        json.dumps(payload),
                        json.dumps(structured_memory),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE ops.chat_sessions
                    SET structured_memory = %s::jsonb,
                        title = CASE
                            WHEN title = 'New conversation' THEN left(%s, 160)
                            ELSE title
                        END,
                        updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s
                    """,
                    (
                        json.dumps(structured_memory),
                        question,
                        session_id,
                        user_id,
                    ),
                )
            connection.commit()
