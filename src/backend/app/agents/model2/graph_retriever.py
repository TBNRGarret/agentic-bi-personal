from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema_graph import SchemaGraph
from .utils import normalize_text, table_name


@dataclass
class GraphRetrievalResult:
    tables: list[dict[str, Any]]
    ranked_tables: list[dict[str, Any]]
    graph_edges: list[dict[str, Any]]
    join_paths: list[dict[str, Any]]
    bridge_tables: list[str] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)


class GraphSchemaRetriever:
    """Graph-aware retrieval over the existing governed metadata catalog."""

    """
    Overview
    1. Receives initial_tables (from DataHub/TableSelector) + SchemaGraph
    2. Graph expansion
        a. Find bridge tables: tables that connect initial tables via shared columns
        b. SchemaGraph.expand_with_bridges(initial_names) returns expanded set
        c. Bridge tables fill gaps for multi-hop joins
    3. Graph re-ranking
        a. Score expanded tables by graph centrality + question relevance
        b. Bounded by max_tables parameter
    4. Subgraph extraction
        a. Extract subgraph containing initial + bridge tables
        b. Find join paths (BFS, max 2 hops)
    5. Return GraphRetrievalResult with:
        a. expanded tables (bounded list)
        b. graph edges from subgraph
        c. join paths between initial tables
        d. bridge table names

    Phase 2 enhancement: uses SchemaGraph for graph-aware table expansion.
    """

    def enhance(
        self,
        question: str,
        *,
        initial_tables: list[dict[str, Any]],
        ranked_tables: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        schema_graph: SchemaGraph,
        max_tables: int,
    ) -> GraphRetrievalResult:
        initial_names = [table_name(table) for table in initial_tables]
        expanded_names = schema_graph.expand_with_bridges(initial_names, question)
        bridge_names = [name for name in expanded_names if name not in set(initial_names)]
        table_by_name = {
            table_name(table): table
            for table in candidates
            if table_name(table)
        }

        expanded_tables = [
            table_by_name.get(name) or schema_graph.table(name)
            for name in expanded_names
        ]
        expanded_tables = [table for table in expanded_tables if table is not None]

        ranked = self._graph_rerank(
            expanded_tables,
            schema_graph=schema_graph,
            question=question,
            bridge_names=set(bridge_names),
        )
        if not ranked:
            ranked = initial_tables

        bounded_names: list[str] = []
        for name in initial_names:
            if name and name not in bounded_names:
                bounded_names.append(name)
        for table in ranked:
            name = table_name(table)
            if name and name not in bounded_names and len(bounded_names) < max_tables:
                bounded_names.append(name)
        for bridge in bridge_names:
            if bridge not in bounded_names and len(bounded_names) < max_tables + 3:
                bounded_names.append(bridge)

        bounded_tables = [
            table_by_name.get(name) or schema_graph.table(name)
            for name in bounded_names
        ]
        bounded_tables = [table for table in bounded_tables if table is not None]

        subgraph = schema_graph.subgraph([table_name(table) for table in bounded_tables])
        join_paths = [
            path.to_dict()
            for path in subgraph.find_join_paths(
                initial_names,
                max_hops=2,
            )
        ]

        existing_ranked_names = [table_name(table) for table in ranked_tables]
        merged_ranked = list(ranked_tables)
        for table in bounded_tables:
            if table_name(table) not in existing_ranked_names:
                merged_ranked.append(table)
                existing_ranked_names.append(table_name(table))

        return GraphRetrievalResult(
            tables=bounded_tables,
            ranked_tables=merged_ranked,
            graph_edges=subgraph.to_edge_list(),
            join_paths=join_paths,
            bridge_tables=bridge_names,
            retrieval={
                "strategy": "graph_schema_retrieval",
                "initialTables": initial_names,
                "expandedTables": bounded_names,
                "bridgeTables": bridge_names,
                "joinPathCount": len(join_paths),
                "graphEdgeCount": len(subgraph.edges),
            },
        )

    def _graph_rerank(
        self,
        tables: list[dict[str, Any]],
        *,
        schema_graph: SchemaGraph,
        question: str,
        bridge_names: set[str],
    ) -> list[dict[str, Any]]:
        terms = set(normalize_text(question).split())
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for position, table in enumerate(tables):
            name = table_name(table)
            base_score = float(table.get("retrievalScore") or 0.0)
            confidence = float(table.get("retrievalConfidence") or 0.0)
            degree_score = min(schema_graph.degree(name) * 0.08, 0.4)
            bridge_score = 0.3 if name in bridge_names else 0.0
            lexical_score = _lexical_graph_score(table, terms)
            score = base_score + confidence + degree_score + bridge_score + lexical_score
            scored.append((score, f"{position:04d}:{name}", table))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        reranked: list[dict[str, Any]] = []
        top_score = scored[0][0] if scored else 0.0
        for score, _key, table in scored:
            graph_confidence = score / max(top_score, 1.0) if score > 0 else 0.0
            reasons = list(table.get("retrievalReasons") or [])
            if schema_graph.degree(table_name(table)):
                reasons.append(f"graph degree {schema_graph.degree(table_name(table))}")
            if table_name(table) in bridge_names:
                reasons.append("bridge table from schema graph path")
            reranked.append(
                {
                    **table,
                    "retrievalScore": round(score, 4),
                    "retrievalConfidence": round(
                        max(float(table.get("retrievalConfidence") or 0.0), graph_confidence),
                        4,
                    ),
                    "retrievalReasons": reasons or ["graph retrieval"],
                }
            )
        return reranked


def _lexical_graph_score(table: dict[str, Any], terms: set[str]) -> float:
    if not terms:
        return 0.0
    haystack = normalize_text(
        " ".join(
            [
                table_name(table),
                str(table.get("description") or ""),
                " ".join(
                    str(column.get("name") or "")
                    for column in table.get("columns") or []
                ),
            ]
        )
    )
    hits = sum(1 for term in terms if len(term) > 2 and term in haystack)
    return min(hits * 0.05, 0.35)
