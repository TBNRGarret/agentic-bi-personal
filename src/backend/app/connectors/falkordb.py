from __future__ import annotations

import json
import time
from typing import Any

from ..config import Settings
from ..agents.model2.utils import column_names, normalize_text, table_name

try:  # redis is optional at import time; the connector degrades cleanly if absent.
    import redis.asyncio as redis
except Exception:  # pragma: no cover - exercised in minimal test envs without redis.
    redis = None  # type: ignore[assignment]


class FalkorGraphConnector:
    """FalkorDB-backed schema graph for Model 2 retrieval.

    The graph stores governed metadata as:
    (Table)-[:HAS_COLUMN]->(Column)
    (Table)-[:JOINS_ON {column}]->(Table)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.host = settings.falkordb_host
        self.port = settings.falkordb_port
        self.graph_name = settings.falkordb_graph_name
        self.enabled = settings.falkordb_enabled
        self.sync_ttl_seconds = settings.falkordb_sync_ttl_seconds
        self._client: Any | None = None
        self._last_sync_key = ""
        self._last_sync_at = 0.0

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def sync_schema(
        self,
        tables: list[dict[str, Any]],
        *,
        datasource: str,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "reason": "FalkorDB is disabled"}
        if redis is None:
            return {"status": "unavailable", "reason": "redis package is not installed"}
        if not tables:
            return {"status": "skipped", "reason": "No tables to sync"}

        sync_key = _sync_key(tables, datasource)
        now = time.monotonic()
        if sync_key == self._last_sync_key and now - self._last_sync_at < self.sync_ttl_seconds:
            return {"status": "cached", "tableCount": len(tables)}

        client = await self._redis()
        await client.ping()
        table_by_name = {table_name(table): table for table in tables if table_name(table)}

        # Remove stale join edges for this datasource, then upsert nodes and edges.
        await self._graph_query(
            "MATCH (a:Table)-[r:JOINS_ON]->(b:Table) "
            f"WHERE a.datasource = {_q(datasource)} AND b.datasource = {_q(datasource)} DELETE r"
        )
        for table in table_by_name.values():
            await self._upsert_table(table, datasource=datasource)
        for edge in _join_edges(list(table_by_name.values())):
            await self._upsert_join(edge)

        self._last_sync_key = sync_key
        self._last_sync_at = now
        return {
            "status": "success",
            "tableCount": len(table_by_name),
            "joinEdgeCount": len(_join_edges(list(table_by_name.values()))),
            "graphName": self.graph_name,
        }

    async def retrieve_tables(
        self,
        question: str,
        *,
        datasource: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.enabled or redis is None:
            return []
        rows = await self._graph_records(
            "MATCH (t:Table) "
            f"WHERE t.datasource = {_q(datasource)} "
            "RETURN t.name, t.payload"
        )
        tables: list[dict[str, Any]] = []
        for row in rows:
            if len(row) < 2:
                continue
            payload = _as_text(row[1])
            try:
                table = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(table, dict) or not table_name(table):
                continue
            score, reasons = _score_table(question, table)
            tables.append(
                {
                    **table,
                    "retrievalScore": round(score, 4),
                    "retrievalConfidence": round(min(score, 1.0), 4),
                    "retrievalReasons": ["falkordb graph retrieval", *reasons],
                }
            )
        tables.sort(key=lambda table: (float(table.get("retrievalScore") or 0), table_name(table)), reverse=True)
        return tables[: max(1, limit)]

    async def persist_query_plan(self, graph: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "reason": "FalkorDB is disabled"}
        if redis is None:
            return {"status": "unavailable", "reason": "redis package is not installed"}
        graph_id = str(graph.get("graphId") or "").strip()
        if not graph_id:
            return {"status": "skipped", "reason": "Query plan graph has no graphId"}

        client = await self._redis()
        await client.ping()
        payload = json.dumps(graph, ensure_ascii=False, default=str)
        await self._graph_query(
            f"MERGE (q:QueryPlan {{id: {_q(graph_id)}}}) "
            f"SET q.question = {_q(graph.get('question'))}, "
            f"q.datasource = {_q(graph.get('datasource'))}, "
            f"q.status = {_q(graph.get('status'))}, "
            f"q.mode = {_q(graph.get('mode'))}, "
            f"q.createdAt = {_q(graph.get('createdAt'))}, "
            f"q.payload = {_q(payload)}"
        )
        await self._graph_query(
            f"MATCH (q:QueryPlan {{id: {_q(graph_id)}}})-[r:HAS_STEP]->(s:PlanStep) "
            "DETACH DELETE s"
        )

        steps = [step for step in graph.get("steps") or [] if isinstance(step, dict)]
        for index, step in enumerate(steps):
            await self._persist_plan_step(graph_id, step, index)
        for step in steps:
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                continue
            for dep_id in step.get("dependsOn") or []:
                await self._graph_query(
                    f"MATCH (s:PlanStep {{id: {_q(_step_node_id(graph_id, step_id))}}}) "
                    f"MATCH (d:PlanStep {{id: {_q(_step_node_id(graph_id, str(dep_id)))}}}) "
                    "MERGE (s)-[:DEPENDS_ON]->(d)"
                )
        return {"status": "success", "graphId": graph_id, "stepCount": len(steps)}

    async def _persist_plan_step(self, graph_id: str, step: dict[str, Any], index: int) -> None:
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            return
        node_id = _step_node_id(graph_id, step_id)
        payload = json.dumps(step, ensure_ascii=False, default=str)
        await self._graph_query(
            f"MERGE (s:PlanStep {{id: {_q(node_id)}}}) "
            f"SET s.stepId = {_q(step_id)}, "
            f"s.label = {_q(step.get('label'))}, "
            f"s.type = {_q(step.get('type'))}, "
            f"s.tool = {_q(step.get('tool'))}, "
            f"s.status = {_q(step.get('status'))}, "
            f"s.order = {int(index)}, "
            f"s.payload = {_q(payload)}"
        )
        await self._graph_query(
            f"MATCH (q:QueryPlan {{id: {_q(graph_id)}}}) "
            f"MATCH (s:PlanStep {{id: {_q(node_id)}}}) "
            f"MERGE (q)-[r:HAS_STEP]->(s) SET r.order = {int(index)}"
        )
        tool = str(step.get("tool") or "").strip()
        if tool:
            await self._graph_query(
                f"MERGE (tool:Tool {{name: {_q(tool)}}}) "
                f"SET tool.kind = {_q(_tool_kind(tool))}"
            )
            await self._graph_query(
                f"MATCH (s:PlanStep {{id: {_q(node_id)}}}) "
                f"MATCH (tool:Tool {{name: {_q(tool)}}}) "
                "MERGE (s)-[:USES_TOOL]->(tool)"
            )
        for table in step.get("tables") or []:
            table_name_value = str(table or "").strip()
            if not table_name_value:
                continue
            await self._graph_query(
                f"MERGE (t:Table {{name: {_q(table_name_value)}}})"
            )
            await self._graph_query(
                f"MATCH (s:PlanStep {{id: {_q(node_id)}}}) "
                f"MATCH (t:Table {{name: {_q(table_name_value)}}}) "
                "MERGE (s)-[:USES_TABLE]->(t)"
            )

    async def _upsert_table(self, table: dict[str, Any], *, datasource: str) -> None:
        name = table_name(table)
        database = str(table.get("database") or table.get("schema") or "")
        description = str(table.get("description") or "")
        payload = json.dumps(table, ensure_ascii=False, default=str)
        search_text = _table_search_text(table)
        await self._graph_query(
            "MERGE (t:Table {name: "
            f"{_q(name)}}}) SET t.database = {_q(database)}, "
            f"t.datasource = {_q(datasource)}, t.description = {_q(description)}, "
            f"t.searchText = {_q(search_text)}, t.payload = {_q(payload)}"
        )
        await self._graph_query(
            f"MATCH (t:Table {{name: {_q(name)}}})-[r:HAS_COLUMN]->(c:Column) DELETE r, c"
        )
        for column in table.get("columns") or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            column_id = f"{name}.{column_name}"
            column_type = str(column.get("type") or "")
            column_description = str(column.get("description") or "")
            column_search = normalize_text(f"{column_name} {column_type} {column_description}")
            await self._graph_query(
                "MERGE (c:Column {id: "
                f"{_q(column_id)}}}) SET c.table = {_q(name)}, c.name = {_q(column_name)}, "
                f"c.type = {_q(column_type)}, c.description = {_q(column_description)}, "
                f"c.searchText = {_q(column_search)}"
            )
            await self._graph_query(
                f"MATCH (t:Table {{name: {_q(name)}}}) "
                f"MATCH (c:Column {{id: {_q(column_id)}}}) "
                "MERGE (t)-[:HAS_COLUMN]->(c)"
            )

    async def _upsert_join(self, edge: dict[str, str]) -> None:
        source = edge["from"]
        target = edge["to"]
        column = edge["column"]
        await self._graph_query(
            f"MATCH (a:Table {{name: {_q(source)}}}) "
            f"MATCH (b:Table {{name: {_q(target)}}}) "
            f"MERGE (a)-[r:JOINS_ON {{column: {_q(column)}}}]->(b) "
            "SET r.kind = 'SHARED_COLUMN'"
        )
        await self._graph_query(
            f"MATCH (a:Table {{name: {_q(target)}}}) "
            f"MATCH (b:Table {{name: {_q(source)}}}) "
            f"MERGE (a)-[r:JOINS_ON {{column: {_q(column)}}}]->(b) "
            "SET r.kind = 'SHARED_COLUMN'"
        )

    async def _redis(self) -> Any:
        if self._client is None:
            if redis is None:
                raise RuntimeError("redis package is not installed")
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                decode_responses=True,
                socket_connect_timeout=self.settings.falkordb_timeout_seconds,
                socket_timeout=self.settings.falkordb_timeout_seconds,
            )
        return self._client

    async def _graph_query(self, query: str) -> Any:
        client = await self._redis()
        return await client.execute_command("GRAPH.QUERY", self.graph_name, query)

    async def _graph_records(self, query: str) -> list[list[Any]]:
        result = await self._graph_query(query)
        if isinstance(result, (list, tuple)) and len(result) >= 2 and isinstance(result[1], list):
            return [row for row in result[1] if isinstance(row, list)]
        return []


def _step_node_id(graph_id: str, step_id: str) -> str:
    return f"{graph_id}:{step_id}"


def _tool_kind(tool: str) -> str:
    if tool in {"clickhouse", "postgres", "datahub", "falkordb"}:
        return "external_tool"
    if "llm" in tool:
        return "llm"
    if tool in {"sql_safety", "response_formatter", "model2_selector"}:
        return "internal_agent"
    return "internal_tool"


def _q(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _sync_key(tables: list[dict[str, Any]], datasource: str) -> str:
    names = sorted(table_name(table) for table in tables if table_name(table))
    column_count = sum(len(table.get("columns") or []) for table in tables)
    return f"{datasource}:{len(names)}:{column_count}:{','.join(names)}"


def _table_search_text(table: dict[str, Any]) -> str:
    pieces = [
        table_name(table),
        str(table.get("database") or table.get("schema") or ""),
        str(table.get("description") or ""),
    ]
    for column in table.get("columns") or []:
        pieces.append(str(column.get("name") or ""))
        pieces.append(str(column.get("type") or ""))
        pieces.append(str(column.get("description") or ""))
    return normalize_text(" ".join(pieces))


def _score_table(question: str, table: dict[str, Any]) -> tuple[float, list[str]]:
    terms = {term for term in normalize_text(question).split() if len(term) > 2}
    haystack = _table_search_text(table)
    score = 0.05
    reasons: list[str] = []
    schema = str(table.get("database") or table.get("schema") or "").lower()
    if schema in {"gold", "vsf_mart"}:
        score += 0.2
        reasons.append("preferred curated layer")
    elif schema in {"silver", "vsf_stg"}:
        score += 0.08
        reasons.append("detail layer candidate")
    hits = [term for term in terms if term in haystack]
    if hits:
        score += min(len(hits) * 0.12, 0.65)
        reasons.append("matched graph terms: " + ", ".join(hits[:5]))
    if any(column.lower().endswith("_id") for column in column_names(table)):
        score += 0.05
        reasons.append("joinable id columns")
    return min(score, 1.0), reasons


def _join_edges(tables: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_name = {table_name(table): set(column_names(table)) for table in tables if table_name(table)}
    names = list(by_name)
    edges: list[dict[str, str]] = []
    for index, source in enumerate(names):
        for target in names[index + 1 :]:
            shared = sorted(
                column
                for column in by_name[source] & by_name[target]
                if _looks_like_join_column(column)
            )
            for column in shared[:4]:
                edges.append({"from": source, "to": target, "column": column})
    return edges


def _looks_like_join_column(column: str) -> bool:
    lowered = column.lower()
    return lowered.endswith("_id") or lowered in {
        "id",
        "run_id",
        "batch_id",
        "trace_id",
        "span_id",
        "session_id",
        "user_id",
        "metric_date",
        "event_date",
        "date",
        "source_app",
        "service_name",
        "model",
    }
