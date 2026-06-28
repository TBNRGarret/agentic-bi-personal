# Model 2 Multi-Table Join Pipeline

This design turns the visual five-step pipeline into concrete Agent 2 contracts.

## 1. Detect business entities

`BusinessEntityDetector` extracts business terms from the question and maps them to governed schema hints. Examples:

- `publisher` -> dimension columns such as `publisher_id`, `publisher_name`
- `doanh thu` / `revenue` -> metric columns such as `total_revenue`, `amount`
- `thang nay` / `this month` -> time columns such as `date`, `metric_date`, `event_time`

The output is `EntityDetectionResult`, embedded into the join plan for downstream retrieval, prompt hints, and refiner validation.

## 2. Traverse graph join paths

`SchemaRetriever` still gathers candidate tables from DataHub/live schema and builds graph edges from shared keys, FK-like columns, and lineage. `JoinPlanner` consumes `retrieval.join_paths` first and falls back to direct `graph_edges` when explicit paths are absent. Paths are ranked by:

- graph/path weight
- coverage of detected business entities
- selected table coverage
- explicit join-column availability
- path length and missing-key penalties

The output is one or more `PlannedJoinPath` objects containing ordered tables, bridge tables, join steps, and confidence.

## 3. Build JOIN hint section

`JoinPlanner` materializes a `hintBlock` and validation contract. The hint block is prepended to the Model 2 schema prompt, but explicitly marked as planning context only. SQL generation must not copy the comment into final SQL.

The validation contract includes:

- `requiredJoinConditions`
- `missingJoinConditions`
- `noCrossJoin`
- `requireExplicitJoinKeys`
- `validateGrainBeforeAggregation`

## 4. Join-aware decomposition

`DecomposerAgent.plan()` now accepts `join_plan`. It injects the join plan into `model2JoinPlan`, `model2JoinHintBlock`, and `model2JoinValidation` inside the structured request plan.

For LLM paths, sub-question solving and final SQL refinement see the join plan. For deterministic fallback, `_join_aware_fallback_sql()` can generate an explicit JOIN SQL using the selected path, aliases, metric columns, dimensions, and time filter.

For paths with more than two joins, the join plan emits CTE-shaped decomposition hints so the LLM can solve 1-2 joins per sub-query before assembling final SQL.

## 5. Refiner validates join conditions

`RefinerAgent` reads `model2JoinValidation` from the request plan. On database errors, it adds join-specific repair instructions to the repair strategy:

- re-check graph join path
- preserve required join conditions
- rewrite fully qualified join conditions with SQL aliases
- avoid CROSS JOIN / comma joins
- validate grain before aggregation

This keeps repair strategy deterministic-first while making LLM repair explicitly join-aware.

## Runtime placement

Current Agent 2 flow:

1. Relevancy + request plan
2. DataHub/live schema retrieval + graph retrieval
3. Hybrid selector prunes schema
4. JoinPlanner creates explicit join plan
5. Decomposer generates SQL with join context
6. Executor runs candidates
7. Refiner repairs using join validation on failure
8. Formatter returns result

## Limits

This is stronger than prompt-only join hints, but still depends on schema metadata quality. Ambiguous paths with similar score are surfaced as warnings; future work can add user confirmation, persistent graph cache, and cardinality statistics.
