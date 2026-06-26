from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .utils import column_names, normalize_text, table_name


@dataclass(frozen=True)
class TableNode:
    name: str
    columns: set[str]
    column_types: dict[str, str]
    pk_columns: set[str]
    fk_columns: dict[str, str]
    schema: str
    description: str
    raw_table: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": sorted(self.columns),
            "columnTypes": self.column_types,
            "pkColumns": sorted(self.pk_columns),
            "fkColumns": self.fk_columns,
            "schema": self.schema,
            "description": self.description,
        }


@dataclass(frozen=True)
class SchemaEdge:
    source: str
    target: str
    edge_type: str
    columns: list[str]
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.source,
            "to": self.target,
            "type": self.edge_type,
            "columns": self.columns,
            "weight": round(self.weight, 4),
        }


@dataclass(frozen=True)
class JoinPath:
    tables: list[str]
    edges: list[SchemaEdge]
    total_weight: float
    bridge_tables: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "edges": [edge.to_dict() for edge in self.edges],
            "totalWeight": round(self.total_weight, 4),
            "bridgeTables": self.bridge_tables,
        }


class SchemaGraph:
    """In-memory schema relationship graph built from governed metadata."""

    def __init__(self) -> None:
        self._nodes: dict[str, TableNode] = {}
        self._edges: list[SchemaEdge] = []
        self._adjacency: dict[str, list[SchemaEdge]] = {}
        self._edge_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()

    @property
    def nodes(self) -> dict[str, TableNode]:
        return dict(self._nodes)

    @property
    def edges(self) -> list[SchemaEdge]:
        return list(self._edges)

    def build_from_metadata(
        self,
        tables: list[dict[str, Any]],
        lineage: list[dict[str, Any]] | None = None,
    ) -> None:
        """Build table graph from DataHub/Postgres/ClickHouse metadata."""
        self._nodes.clear()
        self._edges.clear()
        self._adjacency.clear()
        self._edge_keys.clear()

        column_index: dict[str, list[str]] = {}
        for table in tables:
            node = _node_from_table(table)
            if not node.name:
                continue
            self._nodes[node.name] = node
            for column in node.columns:
                column_index.setdefault(column.lower(), []).append(node.name)

        for node in self._nodes.values():
            for column in node.columns:
                if _looks_like_fk_column(column):
                    for target in self._fk_targets(column, node.name):
                        self._add_edge(
                            node.name,
                            target,
                            "FK",
                            [column],
                            weight=1.0,
                        )

        for column, table_names in column_index.items():
            if column in _TRIVIAL_SHARED_COLUMNS or len(table_names) < 2:
                continue
            if not _useful_shared_column(column):
                continue
            unique_tables = sorted(set(table_names))
            for index, source in enumerate(unique_tables):
                for target in unique_tables[index + 1 :]:
                    self._add_edge(
                        source,
                        target,
                        "SHARED_COLUMN",
                        [column],
                        weight=0.7,
                    )

        for edge in lineage or []:
            upstream = _normalize_table_ref(edge.get("upstream"))
            downstream = _normalize_table_ref(edge.get("downstream"))
            if upstream in self._nodes and downstream in self._nodes:
                self._add_edge(upstream, downstream, "LINEAGE", [], weight=0.5)

    def find_join_paths(
        self,
        source_tables: list[str],
        *,
        max_hops: int = 2,
    ) -> list[JoinPath]:
        """Find shortest high-confidence paths among selected tables."""
        selected = _unique_existing(source_tables, self._nodes)
        paths: list[JoinPath] = []
        seen_pairs: set[tuple[str, str]] = set()
        for index, source in enumerate(selected):
            for target in selected[index + 1 :]:
                pair = tuple(sorted((source, target)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                path = self._shortest_path(source, target, max_hops=max_hops)
                if path is not None:
                    selected_set = set(selected)
                    bridges = [
                        table
                        for table in path.tables
                        if table not in selected_set
                    ]
                    paths.append(
                        JoinPath(
                            tables=path.tables,
                            edges=path.edges,
                            total_weight=sum(edge.weight for edge in path.edges),
                            bridge_tables=bridges,
                        )
                    )
        paths.sort(key=lambda item: (-item.total_weight, len(item.tables), item.tables))
        return paths

    def expand_with_bridges(
        self,
        selected_tables: list[str],
        question: str,
        *,
        max_hops: int = 2,
        max_bridge_tables: int = 3,
    ) -> list[str]:
        """Add bridge tables that connect selected tables through graph paths."""
        selected = _unique_existing(selected_tables, self._nodes)
        if len(selected) < 2:
            return selected

        question_terms = set(normalize_text(question).split())
        bridges: list[str] = []
        for path in self.find_join_paths(selected, max_hops=max_hops):
            for bridge in path.bridge_tables:
                if bridge in selected or bridge in bridges:
                    continue
                if len(bridges) >= max_bridge_tables:
                    break
                if _bridge_is_relevant(self._nodes[bridge], question_terms, path):
                    bridges.append(bridge)
            if len(bridges) >= max_bridge_tables:
                break
        return selected + bridges

    def subgraph(self, table_names: list[str]) -> "SchemaGraph":
        names = set(_unique_existing(table_names, self._nodes))
        graph = SchemaGraph()
        graph._nodes = {
            name: node for name, node in self._nodes.items() if name in names
        }
        for edge in self._edges:
            if edge.source in names and edge.target in names:
                graph._add_edge(
                    edge.source,
                    edge.target,
                    edge.edge_type,
                    edge.columns,
                    edge.weight,
                )
        return graph

    def to_edge_list(self) -> list[dict[str, Any]]:
        return [edge.to_dict() for edge in self._edges]

    def table(self, name: str) -> dict[str, Any] | None:
        node = self._nodes.get(name)
        return node.raw_table if node else None

    def degree(self, name: str) -> int:
        return len(self._adjacency.get(name, []))

    def _fk_targets(self, column: str, source_table: str) -> list[str]:
        base = column.lower()[:-3]
        if not base:
            return []
        matches: list[tuple[int, str]] = []
        for candidate in self._nodes:
            if candidate == source_table:
                continue
            normalized = _table_match_text(candidate)
            singular = normalized.rstrip("s")
            score = 0
            if base == singular or base == normalized:
                score = 4
            elif normalized.endswith(f"_{base}") or singular.endswith(f"_{base}"):
                score = 3
            elif base in normalized.split("_"):
                score = 2
            elif base in normalized:
                score = 1
            if score:
                matches.append((score, candidate))
        matches.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in matches[:3]]

    def _add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        columns: list[str],
        weight: float,
    ) -> None:
        if source == target or source not in self._nodes or target not in self._nodes:
            return
        normalized_columns = tuple(sorted(str(column) for column in columns if column))
        key = (source, target, edge_type, normalized_columns)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        edge = SchemaEdge(
            source=source,
            target=target,
            edge_type=edge_type,
            columns=list(normalized_columns),
            weight=weight,
        )
        self._edges.append(edge)
        self._adjacency.setdefault(source, []).append(edge)

        reverse_key = (target, source, edge_type, normalized_columns)
        if reverse_key not in self._edge_keys:
            self._edge_keys.add(reverse_key)
            reverse = SchemaEdge(
                source=target,
                target=source,
                edge_type=edge_type,
                columns=list(normalized_columns),
                weight=weight,
            )
            self._edges.append(reverse)
            self._adjacency.setdefault(target, []).append(reverse)

    def _shortest_path(
        self,
        source: str,
        target: str,
        *,
        max_hops: int,
    ) -> JoinPath | None:
        queue: deque[tuple[str, list[str], list[SchemaEdge]]] = deque()
        queue.append((source, [source], []))
        best: JoinPath | None = None
        while queue:
            current, tables, edges = queue.popleft()
            if len(edges) > max_hops:
                continue
            if current == target and edges:
                candidate = JoinPath(
                    tables=tables,
                    edges=edges,
                    total_weight=sum(edge.weight for edge in edges),
                    bridge_tables=[],
                )
                if best is None or _path_rank(candidate) > _path_rank(best):
                    best = candidate
                continue
            if len(edges) == max_hops:
                continue
            for edge in sorted(
                self._adjacency.get(current, []),
                key=lambda item: (-item.weight, item.target),
            ):
                if edge.target in tables:
                    continue
                queue.append((edge.target, tables + [edge.target], edges + [edge]))
        return best


_TRIVIAL_SHARED_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "inserted_at",
    "deleted_at",
    "loaded_at",
}


def _node_from_table(table: dict[str, Any]) -> TableNode:
    columns = set(column_names(table))
    column_types = {
        str(column.get("name") or ""): str(column.get("type") or "")
        for column in table.get("columns") or []
        if str(column.get("name") or "")
    }
    pk_columns = {
        column
        for column in columns
        if column == "id" or column.endswith("_pk") or column.endswith("_key")
    }
    fk_columns = {
        column: column[:-3]
        for column in columns
        if _looks_like_fk_column(column)
    }
    return TableNode(
        name=table_name(table),
        columns=columns,
        column_types=column_types,
        pk_columns=pk_columns,
        fk_columns=fk_columns,
        schema=str(table.get("database") or table.get("schema") or ""),
        description=str(table.get("description") or ""),
        raw_table=table,
    )


def _looks_like_fk_column(column: str) -> bool:
    lowered = column.lower()
    return lowered.endswith("_id") and lowered not in {"source_app_id"}


def _useful_shared_column(column: str) -> bool:
    lowered = column.lower()
    return lowered.endswith("_id") or lowered in {
        "run_id",
        "batch_id",
        "trace_id",
        "span_id",
        "session_id",
        "user_id",
        "request_id",
        "metric_date",
        "event_date",
        "date",
        "source_app",
        "service_name",
        "model",
    }


def _normalize_table_ref(value: Any) -> str:
    text = str(value or "").strip()
    if "," in text and text.startswith("urn:"):
        text = text.split(",", 2)[1]
    return text


def _unique_existing(
    table_names: list[str],
    nodes: dict[str, TableNode],
) -> list[str]:
    names: list[str] = []
    for name in table_names:
        if name in nodes and name not in names:
            names.append(name)
    return names


def _table_match_text(name: str) -> str:
    parts = name.lower().split(".")
    compact = parts[-1] if parts else name.lower()
    for prefix in ("dim_", "fct_", "fact_", "gold_", "silver_", "staging_", "stg_"):
        if compact.startswith(prefix):
            compact = compact[len(prefix) :]
    return compact


def _bridge_is_relevant(
    node: TableNode,
    question_terms: set[str],
    path: JoinPath,
) -> bool:
    if any(edge.edge_type in {"FK", "LINEAGE"} for edge in path.edges):
        return True
    haystack = normalize_text(
        " ".join([node.name, node.description, " ".join(node.columns)])
    )
    return any(term and term in haystack for term in question_terms)


def _path_rank(path: JoinPath) -> tuple[float, int]:
    return (path.total_weight, -len(path.tables))
