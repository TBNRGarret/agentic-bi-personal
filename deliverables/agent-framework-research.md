# Agent Framework Research Report

Researched on 2026-06-16 for the Agentic BI Observation Service.

## Recommendation

Use a service-owned raw function-calling loop for the Sprint 2 POC, with the loop structured as a small explicit state machine that can later move to LangGraph if the workflow becomes more branching or long-running.

This recommendation matches the current repo state. The Observation Service is already a FastAPI service with `/chat`, `/query`, and `/schema`, a deterministic terminal agent shell, read-only SQL validation, ClickHouse execution, Langfuse generation logging, and OpenTelemetry tracing. The next step is not a broad autonomous agent; it is a narrow, auditable BI agent that must:

- fetch live metadata before SQL generation,
- choose relevant tables,
- generate ClickHouse SQL,
- validate the SQL,
- execute with timeouts,
- retry on SQL errors at most twice,
- log every request and generated query.

Raw function calling keeps those responsibilities explicit and testable. LangGraph is the best upgrade path once the agent needs durable state, complex routing, human-in-the-loop inspection, multi-step dashboard generation, or long-running workflows.

## Framework Trade-offs

| Option | Strengths | Weaknesses | Fit for this project |
| --- | --- | --- | --- |
| Raw function calling | Minimal dependencies, provider-neutral, full control over retries, SQL policy, logging, metadata retrieval, and data-source routing. Easy to unit test. | You implement state, retries, tool dispatch, memory, and observability conventions yourself. | Best for Sprint 2 POC. The workflow is narrow and safety-critical. |
| LangGraph | Low-level orchestration runtime for long-running, stateful agents; supports durable execution, streaming, persistence, human-in-the-loop, subgraphs, and explicit graph control. Can be used without LangChain. | More moving parts than needed for the first BI loop. Requires learning graph/state abstractions. | Best next step when the POC outgrows a simple state machine. |
| Claude Agent SDK | Provides Claude Code's agent loop, context management, built-in file/shell/code tools, MCP, hooks, permissions, subagents, sessions, and streaming in Python/TypeScript. | Designed for autonomous agents that read/write files and run commands. Too broad for end-user BI SQL; stronger vendor coupling; built-in shell/file affordances are not desirable for a production analytics endpoint. | Useful for internal developer automation, not the runtime BI agent. |
| LlamaIndex | Strong RAG, indexing, retrievers, query engines, structured outputs, and prebuilt `FunctionAgent`/`AgentWorkflow`. Good for document-heavy apps and text-to-SQL prototypes. | Can obscure the exact SQL safety loop and DataHub/ClickHouse-specific constraints. Adds abstractions before the service has proven contracts. | Useful later for schema embedding/retrieval components, not the top-level agent loop now. |
| OpenAI Agents SDK | Good if standardizing on OpenAI-hosted agent primitives, guardrails, tracing, and model/tool integrations. | Less necessary when the project can express the workflow as strict tool calls and service-owned control flow. Provider choice may still be open. | Consider later if the team standardizes on OpenAI end to end. |

## Target Agent Loop

Implement the first production-minded loop as explicit states:

1. `ReceiveQuestion`
   Normalize the question, attach user/session IDs, request ID, and trace ID.

2. `ClassifyIntent`
   Decide whether this is a data question, dashboard request, clarification, unsupported request, or unsafe request. Reject non-read-only data operations early.

3. `FindCandidateTables`
   Call the metadata provider. Return table cards from DataHub, not a full schema dump.

4. `SelectTables`
   Rank candidate tables using a hybrid strategy: lexical match, table/column descriptions, dbt model descriptions, schema tier preference, freshness, lineage proximity, and optional embeddings.

5. `BuildPromptContext`
   Fetch expanded schema only for the top tables. Include ClickHouse dialect rules, allowed operations, business definitions, table descriptions, column types, freshness, quality status, and join hints.

6. `GenerateSQL`
   Ask the LLM for structured output: SQL, tables used, assumptions, chart intent, and confidence. Prefer strict JSON/tool schemas.

7. `ValidateSQL`
   Enforce a local read-only SQL policy before execution. Allow only `SELECT`, `SHOW`, and `DESCRIBE` for the current endpoint. Enforce query timeout, row limit, and forbidden functions/settings.

8. `ExecuteSQL`
   Run against the selected data source through a `DataSource` interface, not directly from the agent.

9. `RepairOrRespond`
   On success, summarize and return table/chart spec. On SQL error, feed back sanitized error context and retry up to two times. On repeated failure, give a graceful explanation and ask for clarification if needed.

10. `LogAndTrace`
    Record question, metadata tables used, generated SQL, validation result, execution time, row count, token usage, retries, model, and final status.

This can be coded as ordinary Python functions/classes first. If it becomes hard to reason about, port states to LangGraph nodes with the same names.

## Schema Context Injection

Use targeted schema context, not full schema dumps.

A full schema dump is acceptable only while the catalog is tiny. It will fail as soon as there are many tables because it wastes tokens, increases table hallucination risk, leaks irrelevant implementation details, and makes the model less likely to focus on the right grain.

Recommended granularity:

- Always provide a compact catalog index to retrieval code, not necessarily the LLM: table name, schema, tier, short description, important columns, dbt model/source type, tags/domains, freshness, quality status.
- Put only the top 3-5 candidate tables in the SQL-generation prompt.
- For each selected table, include only the columns likely needed plus important keys/time columns/measure columns. If unsure, include the full column list for those few tables.
- Include join hints only when known from dbt tests, dbt relationships, naming conventions, or curated metadata.
- Include lineage/freshness as short facts, not graph dumps.
- Prefer marts over staging and staging over raw unless the user explicitly asks for raw telemetry or debugging detail.

Suggested prompt context block:

```text
Question:
<user question>

Available data context:
- Table vsf_mart.fct_llm_interactions
  Purpose: gold fact table for LLM/user interactions.
  Freshness: last_data_updated_at=..., dq_status=pass.
  Columns:
  - event_date Date: date of interaction
  - source_app_id String: app identifier
  - model String: LLM model name
  - total_tokens UInt64: total token count
  - total_cost_usd Decimal: estimated cost
  Upstream: vsf_stg.staging_llm_generations

SQL rules:
- ClickHouse dialect.
- Generate one read-only SELECT.
- Use explicit columns.
- Add LIMIT 500 unless an aggregate returns fewer rows.
- Do not query tables not listed above.
```

## Choosing Relevant Tables

Use a hybrid retrieval pipeline. Do not rely on a single LLM guess.

1. Build table cards from DataHub:
   - fully qualified table name,
   - table description,
   - dbt model description,
   - column names/types/descriptions,
   - schema tier (`vsf_mart`, `vsf_stg`, `vsf_raw`),
   - freshness and quality status,
   - upstream/downstream summaries,
   - tags/domains/owners if present.

2. Score candidates:
   - lexical BM25/token overlap on table/column names and descriptions,
   - semantic embedding similarity over table cards if available,
   - business tier boost for `vsf_mart`,
   - freshness/quality boost for passing, fresh tables,
   - lineage boost when a mart table clearly derives from requested source data,
   - usage/popularity boost later from DataHub ClickHouse query-log usage.

3. Ask the LLM to choose from the ranked candidate list only:
   - allowed table IDs,
   - reason for each table,
   - missing information or ambiguity,
   - confidence.

4. Expand schema for selected tables and generate SQL.

5. If confidence is low or two tables appear equally plausible at different grains, ask a clarifying question instead of guessing.

## Error Correction Loop

Limit SQL repair to two retries after the first failed execution.

Feed back:

- original user question,
- previous SQL,
- ClickHouse error code/message after sanitization,
- exact table context used for generation,
- SQL policy rules,
- retry number,
- instruction to patch the query rather than re-plan from scratch unless the error proves the selected table was wrong.

Do not feed back:

- credentials,
- stack traces,
- arbitrary database internals,
- huge result sets,
- unrelated schema dumps.

Retry policy:

- Retry 1: fix syntax, function, type, identifier, or aggregation errors.
- Retry 2: allow one table/column reselection if the error indicates missing table/column or wrong grain.
- Give up gracefully after two failed repairs, timeout, unsafe SQL, or confidence below threshold.

Graceful failure response:

- explain that the query could not be completed,
- include the high-level reason,
- mention the table context attempted,
- ask one concrete clarification if ambiguity caused the failure,
- log the failed SQL internally, but do not expose sensitive infrastructure details.

## Observation Service Structure

Structure the service around ports/adapters so adding a second ClickHouse cluster or a live API does not change the agent loop.

Suggested modules:

```text
src/backend/app/
  agent/
    loop.py              # state machine
    prompts.py           # prompt/context builders
    schemas.py           # pydantic request/response/tool schemas
    table_selection.py   # ranking and table-card logic
    repair.py            # retry/error correction policy
  metadata/
    base.py              # MetadataProvider protocol
    datahub.py           # DataHub implementation
    clickhouse_fallback.py
  datasources/
    base.py              # DataSource protocol
    clickhouse.py        # ClickHouse executor
    registry.py          # source lookup/routing
  policy/
    sql_guard.py         # read-only SQL validation
    limits.py            # timeout/row limits
  observability/
    logging.py
    tracing.py
```

Core interfaces:

```python
class MetadataProvider(Protocol):
    def search_tables(self, question: str, *, limit: int = 10) -> list[TableCard]: ...
    def get_table_context(self, table_ids: list[str]) -> list[TableContext]: ...
    def get_lineage(self, table_id: str, *, direction: str, depth: int = 2) -> LineageSummary: ...

class DataSource(Protocol):
    name: str
    dialect: str
    def validate(self, sql: str) -> None: ...
    def execute(self, sql: str, *, timeout_seconds: int, row_limit: int) -> QueryResult: ...
```

Adding a second ClickHouse cluster should require:

- a new DataHub `platform_instance`,
- a new `ClickHouseDataSource` config,
- registry entry mapping schemas/tables/platform instance to that source,
- no changes to the agent loop.

Adding a live API should require:

- a `DataSource` adapter that exposes safe read operations,
- metadata table cards or endpoint cards in the `MetadataProvider`,
- tool schema for the API call,
- routing rules that pick the API source when the user's intent matches that domain.

## Concrete Next Steps

1. Convert the current `/chat` terminal planner into a BI planner path while keeping the terminal path disabled or admin-only.
2. Introduce `MetadataProvider` and implement `DataHubMetadataProvider` using the existing `metadata/client/datahub_client.py` logic.
3. Replace direct `/schema` behavior with DataHub-backed schema context plus direct ClickHouse fallback.
4. Add `TableCard`, `TableContext`, `SqlPlan`, `SqlRepairRequest`, and `AgentAnswer` Pydantic models.
5. Implement the state machine with two SQL repair retries.
6. Add contract tests for:
   - table selection uses DataHub metadata,
   - generated SQL only references selected tables,
   - invalid SQL is rejected before execution,
   - ClickHouse error triggers repair,
   - repair stops after two retries,
   - second data source can be registered without changing agent-loop code.

## Sources

- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
- Claude Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- LlamaIndex building an agent: https://developers.llamaindex.ai/python/framework/understanding/agent/
- OpenAI function calling guide: https://platform.openai.com/docs/guides/function-calling
- Existing service docs: `docs/OBSERVATION_SERVICE.md`
- Existing service code: `src/backend/app/main.py`, `src/backend/app/agent.py`, `src/backend/app/sql_guard.py`
